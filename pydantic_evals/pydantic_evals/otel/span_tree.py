from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from textwrap import indent
from typing import Any, Callable

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import SpanContext
from opentelemetry.util.types import AttributeValue

SpanId = int


class SpanNode:
    """A node in the span tree; provides references to parents/children for easy traversal and queries."""

    def __init__(self, span: ReadableSpan):
        self._span = span
        # If a span has no context, it's going to cause problems. We may need to add improved handling of this scenario.
        assert self._span.context is not None, f'{span=} has no context'

        self.parent: SpanNode | None = None
        self.children_by_id: dict[SpanId, SpanNode] = {}  # note: we rely on insertion order to determine child order

    @property
    def children(self) -> list[SpanNode]:
        return list(self.children_by_id.values())

    @property
    def context(self) -> SpanContext:
        """Return the SpanContext of the wrapped span."""
        assert self._span.context is not None
        return self._span.context

    @property
    def parent_context(self) -> SpanContext | None:
        """Return the SpanContext of the parent of the wrapped span."""
        return self._span.parent

    @property
    def span_id(self) -> SpanId:
        """Return the integer span_id from the SpanContext."""
        return self.context.span_id

    @property
    def trace_id(self) -> int:
        """Return the integer trace_id from the SpanContext."""
        return self.context.trace_id

    @property
    def name(self) -> str:
        """Convenience for the span's name."""
        return self._span.name

    @property
    def start_timestamp(self) -> datetime | None:
        """Return the span's start time as a UTC datetime, or None if not set."""
        if self._span.start_time is None:
            return None
        return datetime.fromtimestamp(self._span.start_time / 1e9, tz=timezone.utc)

    @property
    def end_timestamp(self) -> datetime | None:
        """Return the span's end time as a UTC datetime, or None if not set."""
        if self._span.end_time is None:
            return None
        return datetime.fromtimestamp(self._span.end_time / 1e9, tz=timezone.utc)

    @property
    def duration(self) -> timedelta | None:
        """Return the span's duration as a timedelta, or None if start/end not set."""
        if self._span.start_time is None or self._span.end_time is None:
            return None
        ns_diff = self._span.end_time - self._span.start_time
        return timedelta(seconds=ns_diff / 1e9)

    @property
    def attributes(self) -> Mapping[str, AttributeValue]:
        # TODO: Should expose the non-JSON-serialized versions of attributes with nesting
        return self._span.attributes or {}

    def add_child(self, child: SpanNode) -> None:
        """Attach a child node to this node's list of children."""
        self.children_by_id[child.span_id] = child
        child.parent = self

    # -------------------------------------------------------------------------
    # Child queries
    # -------------------------------------------------------------------------
    def find_children(self, predicate: Callable[[SpanNode], bool]) -> list[SpanNode]:
        """Return all immediate children that satisfy the given predicate."""
        return [child for child in self.children if predicate(child)]

    def first_child(self, predicate: Callable[[SpanNode], bool]) -> SpanNode | None:
        """Return the first immediate child that satisfies the given predicate, or None if none match."""
        for child in self.children:
            if predicate(child):
                return child
        return None

    def any_child(self, predicate: Callable[[SpanNode], bool]) -> bool:
        """Returns True if there is at least one child that satisfies the predicate."""
        return self.first_child(predicate) is not None

    # -------------------------------------------------------------------------
    # Descendant queries (DFS)
    # -------------------------------------------------------------------------
    def find_descendants(self, predicate: Callable[[SpanNode], bool]) -> list[SpanNode]:
        """Return all descendant nodes that satisfy the given predicate in DFS order."""
        found: list[SpanNode] = []
        stack = list(self.children)
        while stack:
            node = stack.pop()
            if predicate(node):
                found.append(node)
            stack.extend(node.children)
        return found

    def first_descendant(self, predicate: Callable[[SpanNode], bool]) -> SpanNode | None:
        """DFS: Return the first descendant (in DFS order) that satisfies the given predicate, or `None` if none match."""
        stack = list(self.children)
        while stack:
            node = stack.pop()
            if predicate(node):
                return node
            stack.extend(node.children)
        return None

    def any_descendant(self, predicate: Callable[[SpanNode], bool]) -> bool:
        """Returns `True` if there is at least one descendant that satisfies the predicate."""
        return self.first_descendant(predicate) is not None

    # -------------------------------------------------------------------------
    # Ancestor queries (DFS "up" the chain)
    # -------------------------------------------------------------------------
    def find_ancestors(self, predicate: Callable[[SpanNode], bool]) -> list[SpanNode]:
        """Return all ancestors that satisfy the given predicate."""
        found: list[SpanNode] = []
        node = self.parent
        while node:
            if predicate(node):
                found.append(node)
            node = node.parent
        return found

    def first_ancestor(self, predicate: Callable[[SpanNode], bool]) -> SpanNode | None:
        """Return the closest ancestor that satisfies the given predicate, or `None` if none match."""
        node = self.parent
        while node:
            if predicate(node):
                return node
            node = node.parent
        return None

    def any_ancestor(self, predicate: Callable[[SpanNode], bool]) -> bool:
        """Returns True if any ancestor satisfies the predicate."""
        return self.first_ancestor(predicate) is not None

    # -------------------------------------------------------------------------
    # Matching convenience
    # -------------------------------------------------------------------------
    def matches(self, name: str | None = None, attributes: dict[str, Any] | None = None) -> bool:
        """A convenience method to see if this node's span matches certain conditions.

        - name: exact match for the Span name
        - attributes: dict of key->value; must match exactly.
        """
        if name is not None and self.name != name:
            return False
        if attributes:
            span_attributes = self._span.attributes or {}
            for attr_key, attr_val in attributes.items():
                if span_attributes.get(attr_key) != attr_val:
                    return False
        return True

    # -------------------------------------------------------------------------
    # String representation
    # -------------------------------------------------------------------------
    def repr_xml(
        self,
        include_children: bool = True,
        include_span_id: bool = False,
        include_trace_id: bool = False,
        include_start_timestamp: bool = False,
        include_duration: bool = False,
    ) -> str:
        """Return an XML-like string representation of the node.

        Optionally includes children, span_id, trace_id, start_timestamp, and duration.
        """
        first_line_parts = [f'<SpanNode name={self.name!r}']
        if include_span_id:
            first_line_parts.append(f'span_id={self.span_id:016x}')
        if include_trace_id:
            first_line_parts.append(f'trace_id={self.trace_id:032x}')
        if include_start_timestamp:
            first_line_parts.append(f'start_timestamp={self.start_timestamp!r}')
        if include_duration:
            first_line_parts.append(f'duration={self.duration!r}')

        extra_lines: list[str] = []
        if include_children and self.children:
            first_line_parts.append('>')
            for child in self.children:
                extra_lines.append(
                    indent(
                        child.repr_xml(
                            include_children=include_children,
                            include_span_id=include_span_id,
                            include_trace_id=include_trace_id,
                            include_start_timestamp=include_start_timestamp,
                            include_duration=include_duration,
                        ),
                        '  ',
                    )
                )
            extra_lines.append('</SpanNode>')
        else:
            if self.children:
                first_line_parts.append('children=...')
            first_line_parts.append('/>')
        return '\n'.join([' '.join(first_line_parts), *extra_lines])

    def __str__(self) -> str:
        if self.children:
            return f'<SpanNode name={self.name!r} span_id={self.span_id:016x}>...</SpanNode>'
        else:
            return f'<SpanNode name={self.name!r} span_id={self.span_id:016x} />'

    def __repr__(self) -> str:
        return self.repr_xml()


class SpanTree:
    """A container that builds a hierarchy of SpanNode objects from a list of finished spans.

    You can then search or iterate the tree to make your assertions (using DFS for traversal).
    """

    def __init__(self, spans: list[ReadableSpan] | None = None):
        self.nodes_by_id: dict[int, SpanNode] = {}
        self.roots: list[SpanNode] = []
        if spans:
            self.add_spans(spans)

    def add_spans(self, spans: list[ReadableSpan]) -> None:
        """Add a list of spans to the tree, rebuilding the tree structure."""
        for span in spans:
            node = SpanNode(span)
            self.nodes_by_id[node.span_id] = node
        self._rebuild_tree()

    def _rebuild_tree(self):
        # Ensure spans are ordered by start_timestamp so that roots and children end up in the right order
        nodes = list(self.nodes_by_id.values())
        nodes.sort(key=lambda node: node.start_timestamp or datetime.min)
        self.nodes_by_id = {node.span_id: node for node in nodes}

        # Build the parent/child relationships
        for node in self.nodes_by_id.values():
            parent_ctx = node.parent_context
            if parent_ctx is not None:
                parent_node = self.nodes_by_id.get(parent_ctx.span_id)
                if parent_node is not None:
                    parent_node.add_child(node)

        # Determine the roots
        # A node is a "root" if its parent is None or if its parent's span_id is not in the current set of spans.
        self.roots = []
        for node in self.nodes_by_id.values():
            parent_ctx = node.parent_context
            if parent_ctx is None or parent_ctx.span_id not in self.nodes_by_id:
                self.roots.append(node)

    def flattened(self) -> list[SpanNode]:
        """Return a list of all nodes in the tree."""
        return list(self.nodes_by_id.values())

    def find_all(self, predicate: Callable[[SpanNode], bool]) -> list[SpanNode]:
        """Find all nodes in the entire tree that match the predicate, scanning from each root in DFS order."""
        result: list[SpanNode] = []
        stack = self.roots[:]
        while stack:
            node = stack.pop()
            if predicate(node):
                result.append(node)
            stack.extend(node.children)
        return result

    def find_first(self, predicate: Callable[[SpanNode], bool]) -> SpanNode | None:
        """Find the first node that matches a predicate, scanning from each root in DFS order. Returns `None` if not found."""
        stack = self.roots[:]
        while stack:
            node = stack.pop()
            if predicate(node):
                return node
            stack.extend(node.children)
        return None

    def any(self, predicate: Callable[[SpanNode], bool]) -> bool:
        """Returns True if any node in the tree matches the predicate."""
        return self.find_first(predicate) is not None

    def __str__(self):
        return f'<SpanTree num_roots={len(self.roots)} total_spans={len(self.nodes_by_id)} />'

    def repr_xml(
        self,
        include_children: bool = True,
        include_span_id: bool = False,
        include_trace_id: bool = False,
        include_start_timestamp: bool = False,
        include_duration: bool = False,
    ) -> str:
        """Return an XML-like string representation of the tree, optionally including children, span_id, trace_id, duration, and timestamps."""
        repr_parts = [
            '<SpanTree>',
            *[
                indent(
                    root.repr_xml(
                        include_children=include_children,
                        include_span_id=include_span_id,
                        include_trace_id=include_trace_id,
                        include_start_timestamp=include_start_timestamp,
                        include_duration=include_duration,
                    ),
                    '  ',
                )
                for root in self.roots
            ],
            '</SpanTree>',
        ]
        return '\n'.join(repr_parts)

    def __repr__(self):
        return self.repr_xml()
