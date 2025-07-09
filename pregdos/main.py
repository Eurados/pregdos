import sys
import logging

from pregdos import __version__

logging.basicConfig(level=logging.INFO)
logging.info(f"Starting PregDos version {__version__}")


def main():
    logging.info("Main function started")
    # Here you can add the main logic of your application
    # For example, initializing components, starting services, etc.
    logging.info("Main function completed")


if __name__ == '__main__':
    sys.exit(main())
