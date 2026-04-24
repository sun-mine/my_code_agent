from langchain_core.tools import tool
from ddgs import DDGS

@tool
def search_web(query: str, max_results: int = 5) -> str:
    """
    Search the internet to gather external knowledge, documentation, or answers to questions.
    Returns snippets of relevant web pages.
    """
    try:
        ddgs = DDGS()
        # Use duckduckgo text search
        results = [r for r in ddgs.text(query, max_results=max_results)]
        
        if not results:
            return f"No results found for '{query}'."
            
        output = [f"--- Result {i+1} ---\nTitle: {r.get('title')}\nURL: {r.get('href')}\nSnippet: {r.get('body')}\n" for i, r in enumerate(results)]
        return "\n".join(output)
    except Exception as e:
        return f"Error executing web search: {e}"
