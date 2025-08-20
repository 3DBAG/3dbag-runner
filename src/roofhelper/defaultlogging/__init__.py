import logging


def setup_logging(loglevel: int = logging.INFO) -> logging.Logger:
    """ Create the default logging configuration, output to console with timestamps """
    logging.getLogger('azure.core.pipeline.policies.http_logging_policy').setLevel(logging.ERROR)

    logger = logging.getLogger()
    logger.setLevel(loglevel)  # Set the global logging level

    if not logger.handlers:
        # Create a console handler
        console_handler = logging.StreamHandler()

        # Add a datetime stamp for each message
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(formatter)

        logger.addHandler(console_handler)

    return logger
