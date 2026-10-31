import os
import tempfile

# os testes não devem escrever no log de verdade (logs/organizer.log)
os.environ["LOG_DIR"] = tempfile.mkdtemp(prefix="organizer-logs-")
