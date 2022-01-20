import logging

SNAKEMQ_PORT = 8765
IDENT_KERNEL_MANAGER = "kernel_manager"
IDENT_MAIN = "main"
KERNEL_PID_FILENAME = "kernel_process.pid"

def get_logger():
  logging.basicConfig(format = '[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(message)s')

  logger = logging.getLogger(__name__)
  logger.setLevel(logging.DEBUG)
  return logger
