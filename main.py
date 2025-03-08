import asyncio
import random
import re
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import quote_plus, urljoin, unquote, urlparse

import aiofiles
import aiohttp
from bs4 import BeautifulSoup

from Zlibrary import Zlibrary
from astrbot.api.all import *
from astrbot.api.event.filter import *


@register("ebooks", "buding", "一个功能强大的电子书搜索和下载插件", "1.0.0", "https://github.com/zouyonghe/astrbot_plugin_ebooks")
class ebooks(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.proxy = os.environ.get("https_proxy")
        self.TEMP_PATH = os.path.abspath("data/temp")
        os.makedirs(self.TEMP_PATH, exist_ok=True)

        self.zlibrary = Zlibrary(email=config["zlib_email"], password=config["zlib_password"])
        self.zlibrary.login(email=config["zlib_email"], password=config["zlib_password"])

    async def _search_opds_call(self, query: str, limit: int = None):
        '''Call the OPDS Catalog API to search for eBooks.'''
        opds_url = self.config.get("calibre_url", "http://127.0.0.1:8083")
        search_url = f"{opds_url}/opds/search/{query}"  # 根据实际路径构造 API URL

        async with aiohttp.ClientSession() as session:
            async with session.get(search_url) as response:
                if response.status == 200:
                    content_type = response.headers.get("Content-Type", "")
                    if "application/atom+xml" in content_type:
                        data = await response.text()
                        return self._parse_opds_response(data, limit)  # 调用解析方法
                    else:
                        logger.error(f"Unexpected content type: {content_type}")
                        return None
                else:
                    logger.error(f"OPDS搜索失败，状态码: {response.status}")
                    return None

    def _parse_opds_response(self, xml_data: str, limit: int = None):
        '''Parse the OPDS search result XML data.'''
        opds_url = self.config.get("opds_url", "http://127.0.0.1:8083")

        # Remove illegal characters
        xml_data = re.sub(r'[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD]', '', xml_data)
        # 消除多余空格
        xml_data = re.sub(r'\s+', ' ', xml_data)

        try:
            root = ET.fromstring(xml_data)  # 把 XML 转换为元素树
            namespace = {"default": "http://www.w3.org/2005/Atom"}  # 定义命名空间
            entries = root.findall("default:entry", namespace)  # 查找前20个 <entry> 节点

            results = []
            for entry in entries:
                # 提取电子书标题
                title_element = entry.find("default:title", namespace)
                title = title_element.text if title_element is not None else "未知标题"

                # 提取作者，多作者场景
                authors = []
                author_elements = entry.findall("default:author/default:name", namespace)
                for author in author_elements:
                    authors.append(author.text if author is not None else "未知作者")
                authors = ", ".join(authors) if authors else "未知作者"

                # 提取描述（<summary>）
                summary_element = entry.find("default:summary", namespace)
                summary = summary_element.text if summary_element is not None else "暂无描述"

                # 提取出版日期（<published>）
                published_element = entry.find("default:published", namespace)
                published_date = published_element.text if published_element is not None else "未知出版日期"

                # 提取语言（<dcterms:language>），需注意 namespace
                lang_element = entry.find("default:dcterms:language", namespace)
                language = lang_element.text if lang_element is not None else "未知语言"

                # 提取图书封面链接（rel="http://opds-spec.org/image"）
                cover_element = entry.find("default:link[@rel='http://opds-spec.org/image']", namespace)
                cover_suffix = cover_element.attrib.get("href", "") if cover_element is not None else ""
                if cover_suffix and re.match(r"^/opds/cover/\d+$", cover_suffix):
                    cover_link = urljoin(opds_url, cover_suffix)
                else:
                    cover_link = ""

                # 提取图书缩略图链接（rel="http://opds-spec.org/image/thumbnail"）
                thumbnail_element = entry.find("default:link[@rel='http://opds-spec.org/image/thumbnail']", namespace)
                thumbnail_suffix = thumbnail_element.attrib.get("href", "") if thumbnail_element is not None else ""
                if thumbnail_suffix and re.match(r"^/opds/cover/\d+$", thumbnail_suffix):
                    thumbnail_link = urljoin(opds_url, thumbnail_suffix)
                else:
                    thumbnail_link = ""

                # 提取下载链接及其格式（rel="http://opds-spec.org/acquisition"）
                acquisition_element = entry.find("default:link[@rel='http://opds-spec.org/acquisition']", namespace)
                if acquisition_element is not None:
                    download_suffix = acquisition_element.attrib.get("href", "") if acquisition_element is not None else ""
                    if download_suffix and re.match(r"^/opds/download/\d+/[\w]+/$", download_suffix):
                        download_link = urljoin(opds_url, download_suffix)
                    else:
                        download_link = ""
                    file_type = acquisition_element.attrib.get("type", "未知格式")
                    file_size = acquisition_element.attrib.get("length", "未知大小")
                else:
                    download_link = ""
                    file_type = "未知格式"
                    file_size = "未知格式"

                # 构建结果
                results.append({
                    "title": title,
                    "authors": authors,
                    "summary": summary,
                    "published_date": published_date,
                    "language": language,
                    "cover_link": cover_link,
                    "thumbnail_link": thumbnail_link,
                    "download_link": download_link,
                    "file_type": file_type,
                    "file_size": file_size
                })

            return results[:limit]
        except ET.ParseError as e:
            logger.error(f"解析 OPDS 响应失败: {e}")
            return None

    async def _show_opds_result(self, event: AstrMessageEvent, results: list, guidance: str = None):
        if not results:
            yield event.plain_result("未找到相关的电子书。")

        if len(results) == 1:
            item = results[0]
            chain = [
                Plain(f"{item['title']}")
            ]
            if item.get("cover_link"):
                chain.append(Image.fromURL(item["cover_link"]))
            else:
                chain.append(Plain("\n"))
            chain.append(Plain(f"作者: {item.get('authors', '未知作者')}"))
            chain.append(Plain(f"\n简介: {item.get('summary', '暂无简介')}"))
            chain.append(Plain(f"\n链接(用于下载): {item.get('download_link', '未知链接')}"))
            yield event.chain_result(chain)
        else:
            ns = Nodes([])
            if guidance:
                ns.nodes.append(Node(uin=event.get_self_id(), name="OPDS", content=guidance))
            for idx, item in enumerate(results):
                chain = [Plain(f"{item['title']}")]
                if item.get("cover_link"):
                    chain.append(Image.fromURL(item["cover_link"]))
                else:
                    chain.append(Plain("\n"))
                chain.append(Plain(f"作者: {item.get('authors', '未知作者')}"))
                chain.append(Plain(f"\n简介: {item.get('summary', '暂无简介')}"))
                chain.append(Plain(f"\n链接(用于下载): {item.get('download_link', '未知链接')}"))

                node = Node(
                    uin=event.get_self_id(),
                    name="OPDS",
                    content=chain
                )
                ns.nodes.append(node)
            yield event.chain_result([ns])

    def to_string(self, results: list) -> str:
        """
        将结果列表中的所有项目拼接为字符串。

        Args:
            results (list): 包含字典的结果列表，其中每个字典表示一个条目。

        Returns:
            str: 拼接后的总字符串表示结果。
        """
        if not results:
            return "没有找到结果。"

        result_strings = []
        for item in results:
            part = f"标题: {item.get('title', '未知标题')}\n"
            part += f"作者: {item.get('authors', '未知作者')}\n"
            part += f"描述: {item.get('summary', '暂无描述')}\n"
            part += f"链接: {item.get('download_link', '无下载链接')}\n"
            result_strings.append(part)

        return "\n\n".join(result_strings)

    @command_group("calibre")
    def calibre(self):
        pass

    @calibre.command("search")
    async def search_calibre(self, event: AstrMessageEvent, query: str=None):
        '''搜索 calibre-web 电子书目录'''
        if not query:
            yield event.plain_result("请输入搜索关键词。")
            return

        try:
            results = await self._search_opds_call(quote_plus(query))  # 调用搜索方法
            if not results or len(results) == 0:
                yield event.plain_result("未找到相关的电子书。")
            else:
                async for result in self._show_opds_result(event, results):
                    yield result
        except Exception as e:
            logger.error(f"OPDS搜索失败: {e}")
            yield event.plain_result("搜索过程中出现错误，请稍后重试。")

    @calibre.command("help")
    async def show_help(self, event: AstrMessageEvent):
        '''显示 OPDS 插件帮助信息'''
        help_msg = [
            "📚 ebooks 插件使用指南",
            "该插件通过标准的 OPDS 协议与电子书目录交互，支持搜索、下载和推荐功能。",
            "",
            "🔧 **命令列表**:",
            "- `/opds search [关键词]`：搜索 OPDS 目录中的电子书。例如：`/opds search Python`。",
            "- `/opds download [下载链接/书名]`：通过 OPDS 直接下载电子书。例如：`/opds download http://example.com/path/to/book`。",
            "- `/opds recommend [数量]`：随机推荐指定数量的电子书。例如：`/opds recommend 5`。",
            "- `/opds help`：显示当前插件的帮助信息（即此内容）。",
            "",
            "📒 **注意事项**:",
            "- 下载指令支持直接输入电子书的下载链接或通过精确书名匹配来下载。",
            "- 使用推荐功能时，插件会从现有书目中随机选择电子书。",
        ]
        yield event.plain_result("\n".join(help_msg))

    @calibre.command("download")
    async def download(self, event: AstrMessageEvent, ebook_url: str = None):
        '''通过 OPDS 协议下载 calibre-web 电子书'''
        if not ebook_url:
            yield event.plain_result("请输入电子书的下载链接。")
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(ebook_url) as response:
                    if response.status == 200:
                        # 从 Content-Disposition 提取文件名
                        content_disposition = response.headers.get("Content-Disposition")
                        book_name = None

                        if content_disposition:
                            logger.debug(f"Content-Disposition: {content_disposition}")

                            # 先检查是否有 filename*= 条目
                            book_name_match = re.search(r'filename\*=(?:UTF-8\'\')?([^;]+)', content_disposition)
                            if book_name_match:
                                book_name = book_name_match.group(1)
                                book_name = unquote(book_name)  # 解码 URL 编码的文件名
                            else:
                                # 如果没有 filename*，则查找普通的 filename
                                book_name_match = re.search(r'filename=["\']?([^;\']+)["\']?', content_disposition)
                                if book_name:
                                    book_name = book_name_match.group(1)

                        # 如果未获取到文件名，使用默认值
                        if not book_name or book_name.strip() == "":
                            logger.error(f"无法提取书名，电子书地址: {ebook_url}")
                            yield event.plain_result("无法提取书名，取消发送电子书。")
                            return 
                            
                        # 发送文件到用户
                        file = File(name=book_name, file=ebook_url)
                        yield event.chain_result([file])
                    else:
                        yield event.plain_result(f"无法下载电子书，状态码: {response.status}")
        except Exception as e:
            logger.error(f"下载失败: {e}")
            yield event.plain_result("下载过程中出现错误，请稍后重试。")

    @calibre.command("recommend")
    async def recommend_calibre(self, event: AstrMessageEvent, n: int):
        '''随机推荐 n 本电子书'''
        try:
            # 调用 OPDS 搜索接口，默认搜索所有电子书
            query = "*"  # 空查询，可以调出完整书目
            results = await self._search_opds_call(query)

            # 检查是否有电子书可供推荐
            if not results:
                yield event.plain_result("未找到任何可推荐的电子书。")
                return

            # 限制推荐数量，防止超出实际电子书数量
            if n > len(results):
                n = len(results)

            # 随机选择 n 本电子书
            recommended_books = random.sample(results, n)

            # 显示推荐电子书
            guidance = f"如下是随机推荐的 {n} 本电子书"
            async for result in self._show_opds_result(event, recommended_books, guidance):
                yield result

        except Exception as e:
            logger.error(f"推荐电子书时发生错误: {e}")
            yield event.plain_result("推荐随机电子书时出现错误，请稍后重试。")

    @llm_tool("search_calibre_books")
    async def search_calibre_books(self, event: AstrMessageEvent, query: str):
        """Search books by keywords or title through OPDS.
        When to use:
            Use this method to search for books in the OPDS catalog when user knows the title or keyword.
            This method cannot be used for downloading books and should only be used for searching purposes.
    
        Args:
            query (string): The search keyword or title to find books in the OPDS catalog.
    
        """
        async for result in self.search_calibre(event, query):
            yield result

    @llm_tool("download_calibre_book")
    async def download_calibre_book(self, event: AstrMessageEvent, book_identifier: str):
        """Download a book by a precise name or URL through OPDS.
        When to use:
            Use this method to download a specific book by its name or when a direct download link is available.
    
        Args:
            book_identifier (string): The book name (exact match) or the URL of the book link.
    
        """
        try:
            ebook_url = ""
            # First, determine if the identifier is a URL or a book name
            if book_identifier.lower().startswith("http://") or book_identifier.lower().startswith("https://"):
                ebook_url = book_identifier
            else:
                # Search the book by name
                results = await self._search_opds_call(quote_plus(book_identifier))
                matched_books = [
                    book for book in results if book_identifier.lower() in book["title"].lower()
                ]

                if len(matched_books) == 1:
                    ebook_url = matched_books[0]["download_link"]
                elif len(matched_books) > 1:
                    async for result in self._show_opds_result(event, results, guidance="请使用链接下载电子书。\n"):
                        yield result
                else:
                    yield event.plain_result("未能找到匹配的电子书，请提供准确书名或电子书下载链接。")
                    return
            async for result in self.download(event, ebook_url):
                yield result
        except Exception as e:
            logger.error(f"处理电子书接收过程中出现错误: {e}")
            yield event.plain_result("处理请求时发生错误，请稍后重试或检查输入是否正确。")

    @llm_tool("recommend_books")
    async def recommend_calibre_books(self, event: AstrMessageEvent, n: str = "5"):
        """Randomly recommend n books.
        When to use:
            Use this method to get a random selection of books when users are unsure what to read.
    
        Args:
            n (string): Number of books to recommend (default is 5).
        """
        async for result in self.recommend_calibre(event, int(n)):
            yield result
            
    async def get_liber3_book_details(self, book_ids: list) -> Optional[dict]:
        """通过电子书 ID 获取详细信息"""
        detail_url = "https://lgate.glitternode.ru/v1/book"
        headers = {"Content-Type": "application/json"}
        payload = {"book_ids": book_ids}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(detail_url, headers=headers, json=payload, proxy=self.proxy) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("data", {}).get("book", {})
                    else:
                        logger.error(f"请求电子书详细信息失败，状态码: {response.status}")
                        return None
        except aiohttp.ClientError as e:
            logger.error(f"HTTP 客户端错误: {e}")
        except Exception as e:
            logger.error(f"发生意外错误: {e}")

        return None
    
    async def search_liber3_books_with_details(self, word: str) -> Optional[dict]:
        """搜索电子书并获取前 50 本电子书的详细信息"""
        search_url = "https://lgate.glitternode.ru/v1/searchV2"
        headers = {"Content-Type": "application/json"}
        payload = {
            "address": "",
            "word": word
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(search_url, headers=headers, json=payload, proxy=self.proxy) as response:
                    if response.status == 200:
                        data = await response.json()

                        # 获取电子书 ID 列表
                        book_data = data["data"].get("book", [])
                        if not book_data:
                            logger.info("未找到相关电子书。")
                            return None

                        book_ids = [item.get("id") for item in book_data[:50]]  # 获取前 50 本电子书的 ID
                        if not book_ids:
                            logger.info("未能提取电子书 ID。")
                            return None

                        # 调用详细信息 API
                        detailed_books = await self.get_liber3_book_details(book_ids)
                        if not detailed_books:
                            logger.info("未获取电子书详细信息。")
                            return None

                        # 返回包含搜索结果及详细信息的数据
                        return {
                            "search_results": book_data[:50],  # 原始的前 50 本搜索结果
                            "detailed_books": detailed_books  # 完整详细信息
                        }

                    else:
                        logger.error(f"请求电子书搜索失败，状态码: {response.status}")
                        return None
        except aiohttp.ClientError as e:
            logger.error(f"HTTP 客户端错误: {e}")
        except Exception as e:
            logger.error(f"发生意外错误: {e}")

        return None

    @command_group("liber3")
    def liber3(self):
        pass

    @liber3.command("search")
    async def search_liber3(self, event: AstrMessageEvent, query: str = None):
        """搜索电子书并输出详细信息"""
        if not query:
            yield event.plain_result("请提供电子书关键词以进行搜索。")
            return

        logger.info(f"Received book search query: {query}")
        results = await self.search_liber3_books_with_details(query)

        if not results:
            yield event.plain_result("未找到相关电子书。")
            return

        # 输出搜索结果和详细信息
        search_results = results.get("search_results", [])
        detailed_books = results.get("detailed_books", {})

        ns = Nodes([])

        for index, book in enumerate(search_results, start=1):
            book_id = book.get("id")
            detail = detailed_books.get(book_id, {}).get("book", {})

            chain = [
                Plain(f"标题: {book.get('title', '未知')}\n"),
                Plain(f"作者: {book.get('author', '未知')}\n"),
                Plain(f"语言: {detail.get('language', '未知')}\n"),
                Plain(f"文件大小: {detail.get('filesize', '未知')}\n"),
                Plain(f"文件类型: {detail.get('extension', '未知')}\n"),
                Plain(f"年份: {detail.get('year', '未知')}\n"),
                Plain(f"ID(用于下载): {book_id}"),
            ]

            node = Node(
                uin=event.get_self_id(),
                name="Liber3",
                content=chain
            )
            ns.nodes.append(node)

        yield event.chain_result([ns])

    @liber3.command("download")
    async def download_liber3(self, event: AstrMessageEvent, book_id: str = None):
        if not book_id:
            yield event.plain_result("请提供有效的电子书 ID。")
            return

        # 获取详细的电子书信息
        book_details = await self.get_liber3_book_details([book_id])
        if not book_details or book_id not in book_details:
            yield event.plain_result("无法获取电子书元信息，请检查电子书 ID 是否正确。")
            return

        # 提取电子书信息
        book_info = book_details[book_id].get("book", {})
        book_name = book_info.get("title", "unknown_book").replace(" ", "_")
        extension = book_info.get("extension", "unknown_extension")
        ipfs_cid = book_info.get("ipfs_cid", "")

        if not ipfs_cid or not extension:
            yield event.plain_result("电子书信息不足，无法完成下载。")
            return

        # 构造下载链接
        ebook_url = f"https://gateway-ipfs.st/ipfs/{ipfs_cid}?filename={book_name}.{extension}"

        # 使用 File 对象，通过 chain_result 下载
        file = File(name=f"{book_name}.{extension}", file=ebook_url)
        yield event.chain_result([file])

    @llm_tool("search_liber3_books")
    async def search_liber3_books(self, event: AstrMessageEvent, query: str):
        """Search for books using Liber3 API and return a detailed result list.

        When to use:
            Invoke this tool to locate books based on keywords or titles from Liber3's library.

        Args:
            query (string): The keyword or title to search for books.
        """
        async for result in self.search_liber3(event, query):
            yield result

    @llm_tool("download_liber3_book")
    async def download_liber3_book(self, event: AstrMessageEvent, book_id: str):
        """Download a book using Liber3's API via its unique ID.

        When to use:
            This tool allows you to retrieve a Liber3 book using the unique ID and download it.

        Args:
            book_id (string): A valid Liber3 book ID required to download a book.
        """
        async for result in self.download_liber3(event, book_id):
            yield result

    async def _search_archive_books(self, query: str, limit: int = 20):
        """Search for eBooks through the Archive API and filter files in PDF or EPUB formats.
            Args:
                query (str): Search keyword for titles
                limit (int): Maximum number of results to return
            Returns:
                list: A list containing book information and download links that meet the criteria
        """
        base_search_url = "https://archive.org/advancedsearch.php"
        base_metadata_url = "https://archive.org/metadata/"
        formats = ("pdf", "epub")  # 支持的电子书格式

        params = {
            "q": f'title:"{query}" mediatype:texts',  # 根据标题搜索
            "fl[]": "identifier,title",  # 返回 identifier 和 title 字段
            "sort[]": "downloads desc",  # 按下载量排序
            "rows": limit,  # 最大结果数量
            "page": 1,
            "output": "json"  # 返回格式为 JSON
        }

        async with aiohttp.ClientSession() as session:
            # 1. 调用 Archive 搜索 API
            response = await session.get(base_search_url, params=params, proxy=self.proxy)
            if response.status != 200:
                logger.error(f"搜索 Archive 出现错误，状态码: {response.status}")
                return []

            result_data = await response.json()
            docs = result_data.get("response", {}).get("docs", [])
            if not docs:
                logger.info("未找到与关键词匹配的电子书。")
                return []

            # 2. 根据 identifier 提取元数据
            tasks = [
                self._fetch_metadata(session, base_metadata_url + doc["identifier"], formats) for doc in docs
            ]
            metadata_results = await asyncio.gather(*tasks)

            # 3. 筛选有效结果并返回
            books = [
                {
                    "title": doc.get("title"),
                    "authors": metadata.get("authors"),
                    "download_url": metadata.get("download_url"),
                    "description": metadata.get("description")
                }
                for doc, metadata in zip(docs, metadata_results) if metadata
            ]
            return books

    async def _fetch_metadata(self, session: aiohttp.ClientSession, url: str, formats: tuple) -> dict:
        """
            Retrieve specific eBook formats from the Metadata API and extract covers and descriptions.
            Args:
                session (aiohttp.ClientSession): aiohttp session
                url (str): URL of the Metadata API
                formats (tuple): Required formats (e.g., PDF, EPUB)
            Returns:
                dict: A dictionary with download links, file type, cover, and description
        """
        try:
            response = await session.get(url, proxy=self.proxy)
            if response.status != 200:
                logger.error(f"获取 Metadata 数据失败，状态码: {response.status}")
                return {}

            metadata = await response.json()
            identifier = metadata.get("metadata", {}).get("identifier", None)
            files = metadata.get("files", [])
            description = metadata.get("metadata", {}).get("description", None)
            authors = metadata.get("metadata", {}).get("creator", None)

            # 判断并解析简介
            if isinstance(description, str):
                if self._is_html(description):
                    description = self._parse_html_to_text(description)
                else:
                    description = description.strip()
                description = description[:200] + "..." if len(description) > 200 else description
            else:
                description = "无简介"

            # 提取特定格式文件（如 PDF 和 EPUB）
            for file in files:
                if any(file.get("name", "").lower().endswith(fmt) for fmt in formats):
                    return {
                        "download_url": f"https://archive.org/download/{identifier}/{file['name']}",
                        "description": description,
                        "authors": authors,
                    }

        except Exception as e:
            logger.error(f"获取 Metadata 数据时出现错误: {e}")
        return {}

    def _is_html(self, content):
        """Determine whether a string is in HTML format."""
        if not isinstance(content, str):
            return False
        return bool(re.search(r'<[^>]+>', content))

    def _parse_html_to_text(self, html_content):
        """Parse HTML content into plain text."""
        soup = BeautifulSoup(html_content, "html.parser")
        return soup.get_text().strip()

    @command_group("archive")
    def archive(self):
        pass

    @archive.command("search")
    async def search_archive(self, event: AstrMessageEvent, query: str = None, limit: str = "20"):
        """
            Search for eBooks using the Archive platform, filtering for supported formats.
            Args:
                query (str): The book title or keywords to search for (required).
                limit (str): The result limit, default is 20.
        """
        if not query:
            yield event.plain_result("请输入要搜索的标题或关键词。")
            return

        try:
            limit = int(limit) if limit.isdigit() else 20
            if not (1 <= limit <= 50):  # Validate limit
                yield event.plain_result("请提供 1 到 50 之间的结果数。")
                return

            results = await self._search_archive_books(query, limit)

            if not results:
                yield event.plain_result("未找到符合条件的电子书。")
                return

            # 返回结果到用户
            ns = Nodes([])
            for idx, book in enumerate(results, start=1):
                chain = [
                    Plain(f"{book['title']}\n"),
                    Plain(f"作者: {book.get('authors')}\n"),
                    Plain(f"简介: {book.get('description', '无简介')}\n"),
                    Plain(f"链接(用于下载): {book.get('download_url', '未知')}")
                ]
                node = Node(uin=event.get_self_id(), name="Archive", content=chain)
                ns.nodes.append(node)

            yield event.chain_result([ns])

        except Exception as e:
            logger.error(f"处理 Archive 搜索请求时发生错误: {e}")
            yield event.plain_result("搜索过程中发生错误，请稍后重试。")

    @archive.command("download")
    async def download_archive(self, event: AstrMessageEvent, download_url: str = None):
        """Download an eBook from the Archive platform using a provided link.
            Args:
                download_url (str): The download URL of the eBook.
        """
        if not download_url:
            yield event.plain_result("请提供有效的下载链接。")
            return

        try:
            async with aiohttp.ClientSession() as session:
                # 发出 GET 请求并跟随跳转
                async with session.get(download_url, allow_redirects=True, proxy=self.proxy) as response:
                    if response.status == 200:
                        # 打印跳转后的最终地址
                        ebook_url = str(response.url)
                        logger.info(f"跳转后的下载地址: {ebook_url}")

                        # 从 Content-Disposition 提取文件名
                        content_disposition = response.headers.get("Content-Disposition", "")
                        book_name = None

                        # 提取文件名
                        if content_disposition:
                            logger.debug(f"Content-Disposition: {content_disposition}")
                            book_name_match = re.search(r'filename\*=(?:UTF-8\'\')?([^;]+)', content_disposition)
                            if book_name_match:
                                book_name = unquote(book_name_match.group(1))
                            else:
                                book_name_match = re.search(r'filename=["\']?([^;\']+)["\']?', content_disposition)
                                if book_name_match:
                                    book_name = book_name_match.group(1)

                        # 如果未提取到文件名，尝试从 URL 提取
                        if not book_name or book_name.strip() == "":
                            parsed_url = urlparse(ebook_url)
                            book_name = os.path.basename(parsed_url.path) or "unknown_book"

                        # 构造临时文件路径
                        temp_file_path = os.path.join(self.TEMP_PATH, book_name)

                        # 保存下载文件到本地
                        async with aiofiles.open(temp_file_path, "wb") as temp_file:
                            await temp_file.write(await response.read())

                        # 打印日志确认保存成功
                        logger.info(f"文件已下载并保存到临时目录：{temp_file_path}")

                        # 直接传递本地文件路径
                        file = File(name=book_name, file=temp_file_path)
                        yield event.chain_result([file])
                        os.remove(temp_file_path)

                        # file = File(name=book_name, file=ebook_url)
                        # yield event.chain_result([file])
                    else:
                        yield event.plain_result(f"无法下载电子书，状态码: {response.status}")
        except Exception as e:
            logger.error(f"下载失败: {e}")
            yield event.plain_result(f"下载过程中发生错误：{e}")

    @llm_tool("search_archive_books")
    async def search_archive_books(self, event: AstrMessageEvent, query: str):
        """Search for eBooks using the Archive API.
    
        When to use:
            Utilize this method to search books available in supported formats (such as PDF or EPUB) on the Archive API platform.
    
        Args:
            query (string): The keywords or title to perform the search.
        """
        async for result in self.search_archive(event, query):
            yield result

    @llm_tool("download_archive_book")
    async def download_archive_book(self, event: AstrMessageEvent, download_url: str):
        """Download an eBook from the Archive API using its download URL.
    
        When to use:
            Use this method to download a specific book from the Archive platform using the book's provided download link.
    
        Args:
            download_url (string): A valid and supported Archive book download URL.
        """
        async for result in self.download_archive(event, download_url):
            yield result

    @command_group("zlib")
    def zlib(self):
        pass

    @zlib.command("search")
    async def search_zlib(self, event: AstrMessageEvent, query: str = None, limit: str = "20"):
        """搜索 Zlibrary 电子书并输出详细信息"""
        if not query:
            yield event.plain_result("请提供电子书关键词以进行搜索。")
            return

        try:
            limit = int(limit)
            if not (1 <= limit <= 50):  # Validate limit
                yield event.plain_result("请提供 1 到 50 之间的结果数。")
                return

            logger.info(f"Received Zlibrary book search query: {query}")

            # 调用 Zlibrary 的 search 方法进行搜索
            results = self.zlibrary.search(message=query, limit=limit)

            if not results or not results.get("books"):
                yield event.plain_result("未找到相关电子书。")
                return

            # 处理搜索结果
            books = results.get("books", [])
            ns = Nodes([])

            for index, book in enumerate(books, start=1):
                logger.info(book)
                book_id = book.get("id")
                chain = [Plain(f"标题: {book.get('title', '未知')}\n")]
                if book.get("cover"):
                    chain.append(Image.fromURL(book["cover"]))
                else:
                    chain.append(Plain("\n"))
                chain.append(Plain(f"作者: {book.get('author', '未知')}\n"))
                chain.append(Plain(f"年份: {book.get('year', '未知')}\n"))
                chain.append(Plain(f"出版社: {book.get('publisher', '未知')}\n"))
                chain.append(Plain(f"语言: {book.get('language', '未知')}\n"))
                description = book.get('description', '无简介')
                if isinstance(description, str):
                    description = description.strip()
                    description = description[:200] + "..." if len(description) > 200 else description
                else:
                    description = "无简介"
                chain.append(Plain(f"简介: {description}\n"))
                chain.append(Plain(f"ID(用于下载): {book_id}\n"))
                chain.append(Plain(f"哈希(用于下载): {book.get('hash')}"))

                node = Node(
                    uin=event.get_self_id(),
                    name="Z-Library",
                    content=chain,
                )
                ns.nodes.append(node)

            yield event.chain_result([ns])

        except Exception as e:
            logger.error(f"搜索 Zlibrary 电子书时出错: {e}")
            yield event.plain_result("搜索电子书时发生错误，请稍后再试。")

    @zlib.command("download")
    async def download_zlib(self, event: AstrMessageEvent, book_id: str = None, book_hash: str = None):
        """下载 Z-Library 电子书"""
        if not book_id or not book_hash:
            yield event.plain_result("请使用 /zlib download <id> <hash> 下载。")
            return

        try:
            # 获取电子书详情，确保 ID 合法
            book_details = self.zlibrary.getBookInfo(book_id, hashid=book_hash)
            logger.info(book_details)
            if not book_details:
                yield event.plain_result("无法获取电子书详情，请检查电子书 ID 是否正确。")
                return

            # 下载电子书
            downloaded_book = self.zlibrary.downloadBook({"id": book_id, "hash": book_hash})
            if downloaded_book:
                book_name, book_content = downloaded_book
                # 构造临时文件路径
                temp_file_path = os.path.join(self.TEMP_PATH, book_name)

                # 保存电子书文件
                with open(temp_file_path, "wb") as file:
                    file.write(book_content)

                # 打印日志确认保存成功
                logger.info(f"文件已下载并保存到临时目录：{temp_file_path}")

                # 提醒用户下载完成
                file = File(name=book_name, file=str(temp_file_path))
                yield event.chain_result([file])
                os.remove(temp_file_path)
            else:
                yield event.plain_result("下载失败，请检查提供的电子书信息。")

        except Exception as e:
            logger.error(f"下载 Z-Library 电子书时出错: {e}")
            yield event.plain_result("下载电子书时发生错误，请稍后再试。")

    @llm_tool("search_zlib_books")
    async def search_zlib_books(self, event: AstrMessageEvent, query: str):
        """Search Zlibrary for books using given keywords.

        When to use:
            Use this method to locate books by keywords or title in Z-Library's database.

        Args:
            query (string): The search term to perform the lookup.
        """
        async for result in self.search_zlib(event, query):
            yield result

    @llm_tool("download_zlib_book")
    async def download_zlib_book(self, event: AstrMessageEvent, book_id: str, book_hash: str):
        """Download a book from Z-Library using its book ID and hash.
    
        When to use:
            Use this method for downloading books from Zlibrary with the provided ID and hash.
    
        Args:
            book_id (string): The unique identifier for the book.
            book_hash (string): Hash value required to authorize and retrieve the download.
        """
        async for result in self.download_zlib(event, book_id, book_hash):
            yield result
