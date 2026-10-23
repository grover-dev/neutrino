ffmpeg -i "udp://@0.0.0.0:1234" \
            -c copy \
            -f segment \
            -segment_size 50M \
            -reset_timestamps 1 \
            -strftime 1 \
            "capture_%Y-%m-%d_%H-%M-%S.mp4"
