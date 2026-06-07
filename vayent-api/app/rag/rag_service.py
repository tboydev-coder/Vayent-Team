"""RAG system for schema-based retrieval using Chroma."""
import chromadb
from chromadb.config import Settings as ChromaSettings
import logging
import uuid
from typing import List, Dict, Any, Optional
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.config import get_settings
from app.services.openai_config_service import require_openai_api_key

logger = logging.getLogger(__name__)

# Initialize embeddings
embeddings_model = None


def get_embeddings():
    """Get or initialize OpenAI embeddings."""
    global embeddings_model

    if embeddings_model is None:
        settings = get_settings()
        api_key = require_openai_api_key(settings, logger)
        embeddings_model = OpenAIEmbeddings(
            api_key=api_key,
            model="text-embedding-3-small",
        )

    return embeddings_model


class RAGService:
    """Retrieval-Augmented Generation service for schema context."""

    def __init__(self):
        self.settings = get_settings()
        self.chroma_client = None
        self.collection = None
        self._initialized = False

    def initialize(self):
        """Initialize Chroma client and collection."""
        if self._initialized:
            return

        try:
            # Initialize Chroma with persistent storage
            chroma_settings = ChromaSettings(
                persist_directory=self.settings.chroma_db_path,
                anonymized_telemetry=False,
            )

            self.chroma_client = chromadb.Client(chroma_settings)

            # Get or create collection
            self.collection = self.chroma_client.get_or_create_collection(
                name=self.settings.chroma_collection_name,
                metadata={"hnsw:space": "cosine"},
            )

            self._initialized = True
            logger.info("RAG service initialized")

        except Exception as e:
            logger.error(f"Failed to initialize RAG service: {e}")
            raise

    async def add_schema_to_rag(
        self,
        schema_id: str,
        schema_name: str,
        schema_text: str,
    ) -> None:
        """Add schema metadata to vector store."""
        self.initialize()

        try:
            # Split text into chunks
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=500,
                chunk_overlap=50,
            )
            chunks = text_splitter.split_text(schema_text)

            # Generate embeddings
            embeddings = get_embeddings()
            embedded_chunks = embeddings.embed_documents(chunks)

            # Add to collection
            ids = [f"{schema_id}_{i}" for i in range(len(chunks))]
            metadatas = [{"schema_id": schema_id,
                          "schema_name": schema_name} for _ in chunks]

            self.collection.add(
                ids=ids,
                embeddings=embedded_chunks,
                metadatas=metadatas,
                documents=chunks,
            )

            logger.info(f"Added {len(chunks)} chunks for schema {schema_id}")

        except Exception as e:
            logger.error(f"Failed to add schema to RAG: {e}")
            raise

    async def retrieve_schema_context(
        self,
        query: str,
        connection_id: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant schema context for a user query.

        Returns: List of relevant schema chunks with metadata
        """
        self.initialize()

        try:
            # Embed query
            embeddings = get_embeddings()
            query_embedding = embeddings.embed_query(query)

            # Query collection
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
            )

            # Format results
            context = []
            for i, doc in enumerate(results["documents"][0]):
                distance = results["distances"][0][i]
                metadata = results["metadatas"][0][i]

                context.append({
                    "text": doc,
                    "distance": distance,
                    "schema_id": metadata.get("schema_id"),
                    "schema_name": metadata.get("schema_name"),
                })

            logger.info(f"Retrieved {len(context)} schema chunks for query")
            return context

        except Exception as e:
            logger.error(f"Failed to retrieve schema context: {e}")
            return []

    async def update_schema_in_rag(
        self,
        schema_id: str,
        schema_name: str,
        schema_text: str,
    ) -> None:
        """Update existing schema in vector store."""
        self.initialize()

        try:
            # Delete old schema entries
            await self.delete_schema_from_rag(schema_id)

            # Add updated schema
            await self.add_schema_to_rag(schema_id, schema_name, schema_text)

            logger.info(f"Updated schema {schema_id} in RAG")

        except Exception as e:
            logger.error(f"Failed to update schema in RAG: {e}")
            raise

    async def delete_schema_from_rag(self, schema_id: str) -> None:
        """Delete schema from vector store."""
        self.initialize()

        try:
            # Get IDs to delete
            results = self.collection.get(
                where={"schema_id": schema_id}
            )

            if results["ids"]:
                self.collection.delete(ids=results["ids"])
                logger.info(
                    f"Deleted {len(results['ids'])} chunks for schema {schema_id}")

        except Exception as e:
            logger.error(f"Failed to delete schema from RAG: {e}")
            raise

    def get_collection_stats(self) -> Dict[str, Any]:
        """Get statistics about the RAG collection."""
        self.initialize()

        try:
            count = self.collection.count()
            return {
                "collection_name": self.settings.chroma_collection_name,
                "document_count": count,
            }
        except Exception as e:
            logger.error(f"Failed to get collection stats: {e}")
            return {}


# Singleton instance
rag_service = RAGService()
