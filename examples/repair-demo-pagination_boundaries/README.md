# pagination_boundaries

这是包含故意缺陷的修复演示，8 个测试。

只允许修改：pagination.py。测试保持不变。

任务：先运行 python -B -m unittest discover -v 查看失败，再读取 pagination.py 和 test_pagination.py。只修复 pagination.py，不改测试或其他文件。规则：page从1开始；page_size是正整数；返回当前页items、总页数total_pages和是否还有下一页has_next；总页数向上取整，空列表总页数0；超出末页返回空items和has_next=False；bool不算合法整数。最后重新运行同一测试命令并汇报真实结果。

独立验证：`python -B -m unittest discover -v`。初始失败是演示起点。
