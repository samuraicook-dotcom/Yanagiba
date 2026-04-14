.PHONY: install login paper live backtest test

install:
	pip install -r requirements.txt

login:
	python scripts/login.py

paper:
	python main.py --mode paper

live:
	python main.py --mode live

backtest:
	python scripts/backtest_run.py

test:
	python -m pytest tests/ -v
