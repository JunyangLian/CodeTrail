# Repair task

修复 graph.py 的 dependency_order：输入节点到依赖节点列表，输出每个依赖先于依赖者的拓扑序；包含仅作为依赖出现的节点，不重复节点；任何自环或多节点环抛 ValueError，空图返回空列表，不能修改输入。
允许修改：graph.py。不能修改测试或其他文件。
先运行 python -B -m unittest discover -v 查看失败，再读取代码定位修复；结束前必须重新运行同一测试命令并如实报告。
