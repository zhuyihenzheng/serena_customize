"""
Framework-aware semantic support for Serena.

This package adds *cross-artifact binding* awareness that no Java language server can
provide on its own: the bindings between Java symbols and non-Java artifacts, namely
MyBatis mapper XML statements and Thymeleaf HTML template expressions.

A Java language server (e.g. Eclipse JDT.LS) treats these non-Java artifacts as opaque,
so navigation between a Java mapper interface method and its XML statement, or between a
Thymeleaf expression and the controller-provided model attribute it references, is not
available through the language server. This package resolves those bindings by parsing
the binding skeleton of the non-Java artifacts and combining it with the Java symbols
obtained from the language server.

The functionality is exposed via optional tools (see :mod:`serena.tools.framework_tools`)
and is therefore disabled by default; it must be enabled per project via
``included_optional_tools`` in the project configuration.
"""
