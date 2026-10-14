# What is Bag?

See the [0.28.0 ingestion and scheduling migration note](migrations/next-ingestion-api.md).

[![GitHub](https://img.shields.io/badge/GitHub-genro--bag-blue?logo=github)](https://github.com/genropy/genro-bag)

A Bag is an **intermediate representation** (IR) that bridges the gap between how humans think about structured data and how software implements it.

## The Problem

When you work with configuration files, API responses, form data, or document structures, you're constantly translating between mental models:

- A **configuration** is a tree of settings with names and values
- An **API response** is nested data with metadata attached
- A **form** is a collection of fields with labels, values, and validation rules
- A **document** is hierarchical content with formatting attributes

Each of these has the same fundamental shape: **named things containing values, organized in a hierarchy, with additional properties attached**.

Yet in code, we scatter this across dictionaries, classes, JSON, XML, database rows—each with its own access patterns, serialization rules, and limitations.

## The Bag Abstraction

A Bag unifies these patterns into a single, consistent model:

- **Nodes** — Named containers that hold a value
- **Hierarchy** — Nodes can contain other nodes, forming a tree
- **Attributes** — Each node carries metadata alongside its value
- **Paths** — Navigate the tree with familiar dot notation

This abstraction lets you work with structured data **the way you think about it**, regardless of where it comes from or where it goes.

## Real-World Mapping

| Concept | In the real world | In a Bag |
|---------|-------------------|----------|
| A setting | "The database host is localhost" | Node with path `config.database.host`, value `localhost` |
| A labeled value | "User: Alice (admin)" | Node `user` with value `Alice`, attribute `role=admin` |
| A form field | "Email field, required, must be valid" | Node with value, attributes for validation rules |
| Nested structure | "The server has connection settings" | Parent node containing child nodes |

## Why Not Just Use Dictionaries?

Dictionaries are powerful but low-level. They don't provide:

- **Path-based access** — `d['a']['b']['c']` vs `bag['a.b.c']`
- **Attributes on values** — You can't attach metadata to `d['key']`
- **Ordered iteration** — Dict order isn't always guaranteed or meaningful
- **Change notification** — No built-in way to react when values change
- **Type-agnostic serialization** — You handle JSON, XML, YAML separately

A Bag wraps these concerns into a coherent abstraction.

## Why Not Just Use Classes?

Classes bind structure to behavior. A Bag separates them:

- **Dynamic structure** — Shape isn't fixed at definition time
- **Uniform access** — Same API regardless of content
- **Serialization** — Round-trips to XML, JSON, MessagePack without boilerplate
- **Introspection** — Walk the tree, query attributes, transform at runtime

Use classes when you need fixed contracts. Use Bags when structure emerges from data.

## The Layered Design

Bag provides progressive capability through optional layers:

1. **Core Bag** — The fundamental container with paths, values, attributes
2. **Resolvers** — Values that compute themselves (lazy loading, API calls)
3. **Subscriptions** — React to changes (validation, logging, sync)

Start with core Bag. Add layers only when you need them.

## Where Bag Fits

Bag is not a database, not a schema validator, not a framework. It's a **data structure** that sits between:

- Raw data sources (files, APIs, user input)
- Your application logic
- Output formats (HTML, XML, serialized storage)

It provides a consistent, navigable, observable tree of named values—nothing more, nothing less.

---

**Next:** [Getting Started](getting-started.md) — Learn the three core concepts in 5 minutes

```{toctree}
:maxdepth: 1
:caption: Start Here
:hidden:

getting-started
```

```{toctree}
:maxdepth: 2
:caption: Core Bag
:hidden:

bag/README
bag/basic-usage
bag/paths-and-access
bag/attributes
bag/serialization
bag/extending
bag/examples
bag/faq
bag/architecture
```

```{toctree}
:maxdepth: 2
:caption: Resolvers
:hidden:

resolvers/README
resolvers/builtin
resolvers/custom
resolvers/examples
resolvers/faq
resolvers/architecture
```

```{toctree}
:maxdepth: 2
:caption: Subscriptions
:hidden:

subscriptions/README
subscriptions/events
subscriptions/examples
subscriptions/faq
subscriptions/architecture
```

```{toctree}
:maxdepth: 1
:caption: Reference
:hidden:

reference/why-bag
reference/architecture
reference/benchmarks
reference/full-faq
```

```{toctree}
:maxdepth: 1
:caption: Releases

releases/0.23.2
releases/0.23.0
```
