PYTHON ?= .venv/bin/python

serve:
	docker compose -f docker-compose-local.yml up -d --build
	docker compose -f docker-compose-local.yml logs -f backend

manage:
	docker compose -f docker-compose-local.yml run --rm backend python ./manage.py $(filter-out $@,$(MAKECMDGOALS))

makemigrations:
	docker compose -f docker-compose-local.yml run --rm backend python ./manage.py makemigrations

shell:
	docker compose -f docker-compose-local.yml run --rm backend python ./manage.py shell_plus --ipython

test:
	docker compose -f docker-compose-local.yml run --rm backend pytest

# Runs the same pytest command as CI with deterministic hash seed and strict marker/config checks.
test-ci:
	docker compose -f docker-compose-local.yml up -d db redis
	docker compose -f docker-compose-local.yml run --rm --no-deps \
		-e ENVIRONMENT=dev \
		-e SECRET_KEY=test-secret-key \
		-e DEBUG=True \
		-e SITE_URL=http://localhost:8000 \
		-e POSTGRES_DB=tuxseo \
		-e POSTGRES_USER=tuxseo \
		-e POSTGRES_PASSWORD=tuxseo \
		-e POSTGRES_HOST=db \
		-e POSTGRES_PORT=5432 \
		-e JINA_READER_API_KEY=test-jina-key \
		-e GEMINI_API_KEY=test-gemini-key \
		-e PERPLEXITY_API_KEY=test-perplexity-key \
		-e KEYWORDS_EVERYWHERE_API_KEY=test-keywords-key \
		-e PYTHONHASHSEED=0 \
		backend python -m pytest -q --strict-config --strict-markers

bash:
	docker compose -f docker-compose-local.yml run --rm backend bash

test-webhook:
	docker compose -f docker-compose-local.yml run --rm stripe trigger customer.subscription.created

stripe-sync:
	docker compose -f docker-compose-local.yml run --rm backend python ./manage.py djstripe_sync_models Product Price

restart-stripe:
	docker compose -f docker-compose-local.yml up -d stripe --force-recreate

restart-worker:
	docker compose -f docker-compose-local.yml up -d workers --force-recreate

prod-shell:
	./deployment/prod-shell.sh

test-content-quality:
	ENVIRONMENT=dev \
	SECRET_KEY=test-secret-key \
	DEBUG=True \
	SITE_URL=http://localhost:8000 \
	POSTGRES_DB=tuxseo \
	POSTGRES_USER=tuxseo \
	POSTGRES_PASSWORD=tuxseo \
	POSTGRES_HOST=localhost \
	POSTGRES_PORT=5432 \
	JINA_READER_API_KEY=test-jina-key \
	GEMINI_API_KEY=test-gemini-key \
	PERPLEXITY_API_KEY=test-perplexity-key \
	KEYWORDS_EVERYWHERE_API_KEY=test-keywords-key \
	PYTHONHASHSEED=0 \
	$(PYTHON) -m pytest -q core/tests/test_content_quality_evaluation.py --strict-config --strict-markers
