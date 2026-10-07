Hand written service files.

Cloud server:

- mpeg_ts_ingest.service:
  - This executes mpeg_ts_ingest.sh which listens to a specific udp socket for MPEG TS traffic. It then converts it to mp4 and saves it to disk.
