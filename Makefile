lint:
	flake8 --count --select=E9,F7,F82 --show-source `git ls-files "*.py"`
stylecheck:
	autoflake --check --imports aiohttp,discord,redbot `git ls-files "*.py"`
	isort --check-only `git ls-files "*.py"`
	black --check `git ls-files "*.py" "*.pyi"`
reformat:
	autoflake --in-place --imports=aiohttp,discord,redbot `git ls-files "*.py"`
	isort `git ls-files "*.py"`
	black `git ls-files "*.py" "*.pyi"`
