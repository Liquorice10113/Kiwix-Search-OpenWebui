import os
import requests
import re
import asyncio
from pydantic import BaseModel, Field

from bs4 import BeautifulSoup as Soup
from typing import Callable, Any


class KiwixSearchHelper:
    def __init__(self, kiwix_url: str, event_emitter: Callable[[dict], Any] = None):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
        }
        self.kiwix_url = kiwix_url.rstrip("/")
        self.kiwix_host = self.extract_host(kiwix_url)
        self.event_emitter = event_emitter
        self.https = self.kiwix_url.startswith("https://")

    def extract_host(self, url: str) -> str:
        if "://" in url:
            return url.split("://")[1].split("/")[0]
        return url.split("/")[0]

    async def search(
        self,
        query: str,
        books: str,
    ) -> str:
        for book in books.split(","):
            book = book.strip().rstrip(".zim")
            search_url = f"{self.kiwix_url}/search?books.name={book}&pattern={query}"
            await self.event_emitter(
                {
                    "type": "status",
                    "data": {
                        "status": "in_progress",
                        "description": f'Searching "{query}" in book {book}',
                        # "description": search_url,
                        "done": False,
                    },
                }
            )
            response = requests.get(search_url, headers=self.headers)
            if response.status_code != 200:
                await self.event_emitter(
                    {
                        "type": "status",
                        "data": {
                            "status": "error",
                            "description": f"Failed to search in book {book}. Status code: {response.status_code}",
                            "done": False,
                        },
                    }
                )
                continue
            results = []
            soup = Soup(response.text, "html.parser")
            for i, result in enumerate(soup.find_all("li")):
                title = result.find("a").text.replace("\n", " ").strip()
                if title.startswith("User:") or title.startswith("Talk:"):
                    continue
                link = result.find("a")["href"]
                article_id = link.split("content/")[-1].split("?")[0]
                try:
                    snippet = result.find("cite").text.replace("\n", " ").strip()
                except:
                    snippet = ""
                if snippet:
                    results.append(
                        {
                            "title": title,
                            "snippet": snippet,
                            "from_book": book,
                            "article_id": article_id,
                        }
                    )
        results = results[:10] # limit to 10 results
        formatted_results = self.format_results(results)
        await self.event_emitter(
            {
                "type": "status",
                "data": {
                    "status": "completed",
                    "description": f"Completed search for {len(books.split(','))} books and found {len(results)} results.",
                    "done": True,
                },
            }
        )
        return formatted_results

    async def view_page(
        self, article_id: str, page_content_words_limit: int
    ) -> str:
        """
        View the content of a page by its article ID.
        :param article_id: The article ID of the page to view.
        :param page_content_words_limit: The maximum number of words to include in the returned content.
        :return: The content of the page as a string.
        """
        page_url = f"{self.kiwix_url}/content/{article_id}"
        response = requests.get(page_url, headers=self.headers)
        if response.status_code != 200:
            return f"Failed to retrieve content for article ID {article_id}. Status code: {response.status_code}"
        soup = Soup(response.text, "html.parser")
        content = soup.get_text()
        content = self.text_post_process(content, page_content_words_limit)
        await self.event_emitter(
            {
                "type": "status",
                "data": {
                    "status": "completed",
                    "description": f"{article_id}: approximately {self.tokens_count(content)} tokens.",
                },
            }
        )
        return content

    def text_post_process(self, text: str, page_content_words_limit: int) -> str:
        while "\n\n" in text:
            text = text.replace("\n\n", "\n")
        # filter ref like [1], [2]
        text = re.sub(r"\[\d+\]", " ", text)
        text = text.split()
        text = text[:page_content_words_limit]
        text = " ".join(text)
        return text

    def tokens_count(self, text: str) -> int:
        return int(len(text) / 4)

    def format_results(self, results: list) -> str:
        formatted = ""
        for result in results:
            formatted += f"book_name: {result['from_book']}\n"
            formatted += f"title: {result['title']}\n"
            formatted += f"article_id: {result['article_id']}\n"
            formatted += f"snippet: {result['snippet']}\n"
            formatted += "\n\n---\n\n"
        formatted += "Use the relevant `article_id`, call `kiwix_view_article(article_id)` to view the content of the page.\n"
        return formatted


class Tools:
    class Valves(BaseModel):
        KIWIX_BASE_URL: str = Field(
            default="http://127.0.0.1:80",
            description="The base URL for Kiwix Server.",
        )
        BOOKS: str = Field(
            default="wikipedia_en_all_maxi_2026-02",
            description="Comma-separated list of Kiwix books to search.",
        )
        PAGE_CONTENT_WORDS_LIMIT: int = Field(
            default=5000,
            description="Limit words content for each page.",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def kiwix_search(
        self, query: str, __event_emitter__: Callable[[dict], Any] = None
    ) -> str:
        """
        Kiwix search tool. Use *one or two keywords* for query instead of natural language sentences for better results, avoid "what is", "explain", etc. Eg. User: "Explain options trading" -> query("options trading"). Do not give mutliple queries at once, avoid query("terms1 terms2 terms3 terms4 terms5...") and avoid using too many keywords, again, *one or two keywords* is recommended, long list of keywords may cause no results.
        :param query: The search query string.
        :return: The search results as a formatted string.
        """
        helper = KiwixSearchHelper(
            self.valves.KIWIX_BASE_URL, event_emitter=__event_emitter__
        )
        query = query.strip().split()[:5]  # limit to 5 words
        query = " ".join(query)
        results = await helper.search(
            query=query,
            books=self.valves.BOOKS,
        )
        if len(results) == 0:
            results = "No results found. Hint: use one or two keywords for query, long list of keywords may cause no results."
        return results

    async def kiwix_view_article(
        self, article_id: str, __event_emitter__: Callable[[dict], Any] = None
    ) -> str:
        """
        View the content of a Kiwix article by its article ID.
        :param article_id: The article ID of the page to view.
        :return: The content of the page as a string.
        """
        helper = KiwixSearchHelper(
            self.valves.KIWIX_BASE_URL, event_emitter=__event_emitter__
        )
        content = await helper.view_page(
            article_id=article_id,
            page_content_words_limit=self.valves.PAGE_CONTENT_WORDS_LIMIT,
        )
        return content


if __name__ == "__main__":

    async def event_emitter(event: dict):
        print(event)

    tool = Tools()
    query = "pip"
    results = asyncio.run(
        tool.kiwix_search(query=query, __event_emitter__=event_emitter)
    )
    print("Final Results:")
    print(results)
    result = asyncio.run(
        tool.kiwix_view_article(
            article_id="wikipedia_en_all_maxi_2026-02/Pip_(Moby-Dick_character)",
            __event_emitter__=event_emitter,
        )
    )
    print("Article Content:")
    print(result)
