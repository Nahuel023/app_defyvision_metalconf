import sys

sys.argv = ["metalconf", "run"]

from src.main import main

sys.exit(main())
