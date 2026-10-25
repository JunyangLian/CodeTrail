def dependency_order(graph):
    done = set()
    result = []
    def visit(node):
        if node in done:
            return
        done.add(node)
        result.append(node)
        for child in graph.get(node, []):
            visit(child)
    for node in graph:
        visit(node)
    return result
