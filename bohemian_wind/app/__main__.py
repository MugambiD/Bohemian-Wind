"""python -m bohemian_wind.app [--port N] [--no-browser] [--project FOLDER]"""
from ..__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(["app", *__import__("sys").argv[1:]]))
