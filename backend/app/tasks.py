from celery import Celery

from backend.app.config import settings

celery_app = Celery("docmind", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(task_serializer="json", accept_content=["json"], result_serializer="json",
  task_acks_late=True, worker_prefetch_multiplier=1)

@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_document(self, document_id: str):
    import asyncio

    from backend.app.ingestion import ingest_document
    asyncio.run(ingest_document(document_id))
