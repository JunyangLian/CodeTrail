# user_lookup_contract

这是包含故意缺陷的修复演示，8 个测试。

只允许修改：repository.py, service.py。测试保持不变。

任务：先运行 python -B -m unittest discover -v，再读取 service.py、repository.py、id_normalization.py 和 test_user_lookup.py。只修改 service.py 和 repository.py，不改测试、id_normalization.py 或其他文件。修复跨文件调用契约：get_user(users,user_id)应使用既有normalize_user_id查找规范化键；display_name(users,user_id)调用仓储后返回用户name，找不到返回None；None ID仍由规范化函数抛ValueError；不修改输入字典。最后运行同一测试命令，汇报实际结果。

独立验证：`python -B -m unittest discover -v`。初始失败是演示起点。
