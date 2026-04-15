from app.rag.retriever import SimpleRetriever
from app.services.llm import TogetherLLM


class QAService:
    def __init__(
        self,
        retriever: SimpleRetriever | None = None,
        llm: TogetherLLM | None = None,
    ) -> None:
        self.retriever = retriever or SimpleRetriever()
        self.llm = llm or TogetherLLM()

    def answer(self, question: str) -> str:
        chunks = self.retriever.search(question, limit=3)
        if not chunks:
            return 'No relevant context found for this question.'

        context_block = '\n'.join(f'- {item.text}' for item in chunks)
        prompt = (
            'Answer using only the provided context.\n\n'
            f'Context:\n{context_block}\n\n'
            f'Question: {question}'
        )
        return self.llm.generate(prompt)
