lint:
	flake8 --count --select=E9,F7,F82 --show-source `git ls-files "*.py"`
stylecheck:
	autoflake --check --imports aiohttp,discord,redbot `git ls-files "*.py"`
	isort --check --profile black --line-length 99 `git ls-files "*.py"`
	black --check --target-version py38 --line-length 99 `git ls-files "*.py" "*.pyi"`
reformat:
	autoflake --in-place --imports=aiohttp,discord,redbot `git ls-files "*.py"`
	isort --profile black --line-length 99 `git ls-files "*.py"`
	black --target-version py38 --line-length 99 `git ls-files "*.py" "*.pyi"`
