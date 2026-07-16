#!/bin/sh

# 只生成磁盘使用清单和告警，不自动删除微信文件。
python /operations/storage_audit.py > /data/storage-audit-latest.json
