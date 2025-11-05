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
        results_per_book: int,
        page_content_words_limit: int,
    ) -> str:
        final_results = []
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
                try:
                    snippet = result.find("cite").text.replace("\n", " ").strip()
                except:
                    snippet = ""
                results.append(
                    {
                        "title": title,
                        "link": link,
                        "snippet": snippet,
                        "from_book": book,
                    }
                )

            # Simple rerank
            results = [(i, 0) for i in results]
            for i, (result, _) in enumerate(results):
                score = 0
                title = result["title"].lower()
                snippet = result["snippet"].lower()
                query_lower = query.lower()
                for term in query_lower.split():
                    if term in title:
                        score += 5
                score -= len(title) // 20  # shorter title better
                results[i] = (result, score)
            results.sort(key=lambda x: x[1], reverse=True)

            # print("Reranked results:")
            # for res, sc in results:
            #     print(f"Score: {sc}, Title: {res['title']}")
            # Fetch page contents for top results
            for result, _ in results[:results_per_book]:
                title = result["title"]
                link = result["link"]
                snippet = result["snippet"]
                from_book = result["from_book"]
                # Extract the page content
                if self.https:
                    page_response = requests.get(
                        f"https://{self.kiwix_host}{link}", headers=self.headers
                    )
                else:
                    page_response = requests.get(
                        f"http://{self.kiwix_host}{link}", headers=self.headers
                    )
                if page_response.status_code == 200:
                    page_soup = Soup(page_response.text, "html.parser")
                    content = page_soup.get_text()
                    content = self.text_post_process(content, page_content_words_limit)
                else:
                    content = "Failed to retrieve content"
                final_results.append(
                    {
                        "title": title,
                        "link": (
                            f"https://{self.kiwix_host}{link}"
                            if self.https
                            else f"http://{self.kiwix_host}{link}"
                        ),
                        "content": content,
                        "from_book": book,
                    }
                )
        formatted_results = self.format_results(final_results)
        await self.event_emitter(
            {
                "type": "status",
                "data": {
                    "status": "completed",
                    "description": f"Completed search for {len(books.split(','))} books and found {len(final_results)} results. Approximately {self.tokens_count( formatted_results )} tokens.",
                    "done": True,
                },
            }
        )
        return formatted_results

    def text_post_process(self, text: str, page_content_words_limit: int) -> str:
        while "\n\n" in text:
            text = text.replace("\n\n", "\n")
        # filter ref like [1], [2]
        text = re.sub(r"\[\d+\]", " ", text)
        text = text[:page_content_words_limit]
        return text

    def tokens_count(self, text: str) -> int:
        return len(text.split())

    def format_results(self, results: list) -> str:
        formatted = ""
        for result in results:
            formatted += f"Title: {result['title']}\n"
            formatted += f"Link: {result['link']}\n"
            formatted += f"Content: {result['content']}\n"
            formatted += f"From Book: {result['from_book']}\n"
            formatted += "\n\n---\n\n"
        return formatted


class Tools:
    class Valves(BaseModel):
        KIWIX_BASE_URL: str = Field(
            default="http://127.0.0.1:80",
            description="The base URL for Kiwix",
        )
        BOOKS: str = Field(
            default="wikipedia_en_all_maxi_2025-08",
            description="Comma-separated list of Kiwix books to search.",
        )
        RESULTS_PER_BOOK: int = Field(
            default=3,
            description="The number of results to return per Kiwix book.",
        )
        PAGE_CONTENT_WORDS_LIMIT: int = Field(
            default=5000,
            description="Limit words content for each page.",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def search(
        self, query: str, __event_emitter__: Callable[[dict], Any] = None
    ) -> list:
        """
        Kiwix search tool. Use one or two keyword for query instead of natural language sentences for better results, avoid "what is", "explain", etc. Eg. User: "Explain options trading" -> query: "options trading". Do not give mutliple queries at once, avoid "terms1, terms2".
        :param query: The search query string.
        :return: The search results as a formatted string.
        """
        helper = KiwixSearchHelper(
            self.valves.KIWIX_BASE_URL, event_emitter=__event_emitter__
        )
        results = await helper.search(
            query=query,
            books=self.valves.BOOKS,
            results_per_book=self.valves.RESULTS_PER_BOOK,
            page_content_words_limit=self.valves.PAGE_CONTENT_WORDS_LIMIT,
        )
        return results


if __name__ == "__main__":
    async def event_emitter(event: dict):
        print(event)
    tool = Tools()
    query = "pip"
    results = asyncio.run(tool.search(query=query, __event_emitter__=event_emitter))
    # print("Final Results:")
    # print(results)
