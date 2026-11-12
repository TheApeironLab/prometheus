# Keep upstream build/test targets intact.
export VERSION CHANNEL ARCH OUTPUT PACKAGE ARCHIVE_SHA256 MATERIALS
.PHONY: help check images chart package package-slim verify deployment-test
help check images chart package package-slim verify deployment-test:
	python3 -B release/package/entry.py $@
