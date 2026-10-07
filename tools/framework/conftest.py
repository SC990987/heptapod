"""pytest configuration for tools/framework.

The files under template/ are the contents of generated projects, not part of this
package: they import a placeholder package and must not be collected or imported here.
"""
collect_ignore_glob = ["template/*"]
