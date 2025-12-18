

check:
	poetry run pre-commit run -a


test-llm-investigate:
	poetry run pytest tests/llm/test_investigate.py -n 6 -vv

test-llm-ask-holmes:
	poetry run pytest tests/llm/test_ask_holmes.py -n 6 -vv

test-without-llm:
	poetry run pytest tests -m "not llm"

docs:
	poetry run mkdocs serve --dev-addr=127.0.0.1:7000

docs-build:
	poetry run mkdocs build

docs-strict:
	poetry run mkdocs serve --dev-addr=127.0.0.1:7000 --strict
