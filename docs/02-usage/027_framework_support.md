# Framework-Aware Support (MyBatis & Thymeleaf)

Serena's symbolic tools are backed by a language server, which understands symbols *within* a
single language. For Java/Spring projects, however, much of the meaningful structure lives in the
*bindings between* a Java symbol and a non-Java artifact — bindings that no Java language server
can resolve, because it treats those artifacts as opaque:

* **MyBatis**: a Java mapper interface method (e.g. `UserMapper.findById`) is bound to a
  `<select id="findById">` statement in a separate XML file.
* **Thymeleaf**: an HTML template references model attributes (e.g. `${user.name}`, `*{email}`)
  that a controller places into the model.

The framework-aware support adds tools that resolve these cross-artifact bindings in both
directions. This support is **experimental** and **disabled by default**.

## Enabling the tools

The framework tools are optional tools and must be enabled per project. Add them to
`included_optional_tools` in the project configuration (`.serena/project.yml`):

```yaml
included_optional_tools:
  - find_mapper_xml_for_method
  - find_mapper_method_for_xml
  - find_thymeleaf_bindings_for_model_attribute
  - list_thymeleaf_model_attributes
```

## The tools

### MyBatis

* **`find_mapper_xml_for_method`** — given a Java mapper interface file and a method name, returns
  the bound XML statement's file and line number.
* **`find_mapper_method_for_xml`** — given a mapper XML file and a statement `id`, returns the
  fully qualified Java method it implements (which can then be located with `find_symbol`).

The binding is established by matching the mapper XML's `<mapper namespace="...">` (the fully
qualified interface name) plus the statement `id` (the method name) against the Java interface.

### Thymeleaf

* **`find_thymeleaf_bindings_for_model_attribute`** — given a model attribute name (e.g. `user`),
  returns the template locations that reference it.
* **`list_thymeleaf_model_attributes`** — given a template file, lists the distinct model
  attributes it references, so the controller code that supplies them can be located with
  `find_symbol`.

## What is parsed (and what is not)

To stay token-efficient, only the *binding skeleton* of each artifact is parsed:

* MyBatis: the mapper namespace and the `id`/type/line of each `<select>`/`<insert>`/`<update>`/
  `<delete>` statement. SQL bodies, `<sql>` fragments and dynamic tags are ignored.
* Thymeleaf: the root variables of `${...}` and `*{...}` expressions (the latter resolved against
  the enclosing `th:object`). Static HTML, CSS/Tailwind classes and text nodes are ignored.

## Known limitations

This is an initial milestone focused on navigation; the following are intentionally out of scope:

* Editing/renaming across the binding (the tools are read-only navigation).
* MyBatis `<resultMap>` references and bindings inside dynamic SQL tags.
* Thymeleaf `th:with` local variables (only `th:each` loop variables are currently excluded),
  expression utility objects (`#dates`, ...) and link expressions (`@{...}`).
* The binding index is rebuilt on each tool invocation (no caching yet).
