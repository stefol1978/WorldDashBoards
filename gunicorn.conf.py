import os
bind = "0.0.0.0:" + os.environ.get("PORT", "10000")
accesslog = None
errorlog = "-"
loglevel = "info"
workers = 1
