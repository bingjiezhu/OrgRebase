"""Allow `python -m orgrebase` as the same entry as the `orgrebase` console script."""

from orgrebase.cli import main

if __name__ == "__main__":
    main()
