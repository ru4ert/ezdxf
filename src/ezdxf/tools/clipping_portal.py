#  Copyright (c) 2024, Manfred Moitzi
#  License: MIT License
from __future__ import annotations
from typing import Optional, Iterable, Iterator, Sequence
import abc

from ezdxf.math import Matrix44, Vec2, BoundingBox2d, UVec
from ezdxf.math.clipping import ClippingRect2d as _ClippingRect2d
from ezdxf.npshapes import NumpyPath2d, NumpyPoints2d

__all__ = ["ClippingShape", "ClippingPortal", "ClippingRect"]


class ClippingShape(abc.ABC):
    """The ClippingShape defines a single clipping path and executes the clipping on
    basic geometries:

    - point: a single point
    - line: a line between two vertices
    - polyline: open polyline with one or more straight line segments
    - polygon: closed shape with straight line as edges
    - path: open shape with straight lines and Bezier-curves as segments
    - filled-path: closed shape with straight lines and Bezier-curves as edges

    Difference between open and closed shapes:

        - an open shape is treated as a linear shape without a filling
        - clipping an open shape returns one or more open shapes
        - a closed shape is treated as a filled shape, where the first vertex is
          coincident to the last vertex.
        - clipping a closed shape returns one or more closed shapes

    Notes:

        An arbitrary clipping polygon can split any basic geometry (except point) into
        multiple parts.

        All current implemented clipping algorithms flatten Bezier-curves into polylines.

    """

    remove_outside: bool = True
    # - True: remove geometry outside the clipping shape
    # - False: remove geometry inside the clipping shape

    @abc.abstractmethod
    def clip_point(self, point: Vec2) -> Optional[Vec2]:
        ...

    @abc.abstractmethod
    def clip_line(self, start: Vec2, end: Vec2) -> Sequence[tuple[Vec2, Vec2]]:
        ...

    @abc.abstractmethod
    def clip_polyline(self, points: NumpyPoints2d) -> Sequence[NumpyPoints2d]:
        ...

    @abc.abstractmethod
    def clip_polygon(self, points: NumpyPoints2d) -> Sequence[NumpyPoints2d]:
        ...

    @abc.abstractmethod
    def clip_paths(
        self, paths: Iterable[NumpyPath2d], max_sagitta: float
    ) -> Iterator[NumpyPath2d]:
        ...

    @abc.abstractmethod
    def clip_filled_paths(
        self, paths: Iterable[NumpyPath2d], max_sagitta: float
    ) -> Iterator[NumpyPath2d]:
        ...


class ClippingPortal:
    """The ClippingPortal manages a clipping path hierarchy."""

    def __init__(self) -> None:
        self._stack: list[tuple[ClippingShape, Matrix44 | None]] = []
        self.portal: ClippingShape | None = None
        self.transform: Matrix44 | None = None

    @property
    def is_active(self) -> bool:
        return self.portal is not None

    def push(self, portal: ClippingShape, transform: Matrix44 | None) -> None:
        if self.portal is not None:
            self._stack.append((self.portal, self.transform))
        self.portal = portal
        self.transform = transform

    def pop(self) -> None:
        if self._stack:
            self.portal, self.transform = self._stack.pop()
        else:
            self.portal = None
            self.transform = None

    def clip_point(self, point: Vec2) -> Optional[Vec2]:
        if self.transform:
            point = Vec2(self.transform.transform(point))
        if self.portal:
            return self.portal.clip_point(point)
        return point

    def clip_line(self, start: Vec2, end: Vec2) -> Sequence[tuple[Vec2, Vec2]]:
        m = self.transform
        if m:
            start, end = m.fast_2d_transform((start, end))
        if self.portal:
            return self.portal.clip_line(start, end)
        return ((start, end),)

    def clip_polyline(self, points: NumpyPoints2d) -> Sequence[NumpyPoints2d]:
        if self.transform is not None:
            points.transform_inplace(self.transform)
        if self.portal:
            return self.portal.clip_polyline(points)
        return (points,)

    def clip_polygon(self, points: NumpyPoints2d) -> Sequence[NumpyPoints2d]:
        if self.transform is not None:
            points.transform_inplace(self.transform)
        if self.portal:
            return self.portal.clip_polygon(points)
        return (points,)

    def clip_paths(
        self, paths: Iterable[NumpyPath2d], max_sagitta: float
    ) -> Iterator[NumpyPath2d]:
        paths = _transform_paths(list(paths), self.transform)
        if self.portal:
            return self.portal.clip_paths(paths, max_sagitta)
        return iter(paths)

    def clip_filled_paths(
        self, paths: Iterable[NumpyPath2d], max_sagitta: float
    ) -> Iterator[NumpyPath2d]:
        paths = _transform_paths(list(paths), self.transform)
        if self.portal:
            return self.portal.clip_filled_paths(paths, max_sagitta)
        return iter(paths)

    def transform_matrix(self, m: Matrix44) -> Matrix44:
        if self.transform is not None:
            return m @ self.transform
        return m


def _transform_paths(paths: list[NumpyPath2d], m: Matrix44 | None) -> list[NumpyPath2d]:
    if m is None:
        return paths
    for path in paths:
        path.transform_inplace(m)
    return paths


class ClippingRect(ClippingShape):
    """Represents a rectangle as clipping shape where the edges are parallel to
    the x- and  y-axis of the coordinate system.

    The current implementation does not support removing the content inside the
    clipping shape (remove_outside=False).

    """

    def __init__(self, vertices: Iterable[UVec], remove_outside=True) -> None:
        self.remove_outside = remove_outside
        self.remove_all = False
        self.remove_none = False
        bbox = BoundingBox2d(vertices)
        if bbox.extmin is None or bbox.extmax is None:
            raise ValueError("clipping box not detectable")
        size: Vec2 = bbox.size
        if size.x * size.y < 1e-9:
            if self.remove_outside:
                self.remove_all = True
            else:  # remove inside
                self.remove_none = True
        self.clipper = _ClippingRect2d(bbox.extmin, bbox.extmax)

    def clip_point(self, point: Vec2) -> Optional[Vec2]:
        if self.remove_all:
            return None
        elif self.remove_none:
            return point

        is_inside = self.clipper.is_inside(Vec2(point))
        if self.remove_outside:
            if not is_inside:
                return None
        else:  # remove inside
            if is_inside:
                return None
        return point

    def clip_line(self, start: Vec2, end: Vec2) -> Sequence[tuple[Vec2, Vec2]]:
        if self.remove_all:
            return tuple()
        if self.remove_none:
            return ((start, end),)

        # rectangular clipping box returns always a single line segment or an empty tuple
        cropped_segment = self.clipper.clip_line(start, end)
        if cropped_segment:
            return (cropped_segment,)  # type: ignore
        return tuple()

    def clip_polyline(self, points: NumpyPoints2d) -> Sequence[NumpyPoints2d]:
        if self.remove_all:
            return (NumpyPoints2d(tuple()),)
        if self.remove_none:
            return (points,)

        clipper = self.clipper
        extmin, extmax = points.extents()
        if not clipper.is_inside(extmin) or not clipper.is_inside(extmax):
            return [
                NumpyPoints2d(part) for part in clipper.clip_polyline(points.vertices())
            ]
        return (points,)

    def clip_polygon(self, points: NumpyPoints2d) -> Sequence[NumpyPoints2d]:
        if self.remove_all:
            return (NumpyPoints2d(tuple()),)
        if self.remove_none:
            return (points,)

        clipper = self.clipper
        extmin, extmax = points.extents()
        if not clipper.is_inside(extmin) or not clipper.is_inside(extmax):
            # ClippingRect2d handles only convex clipping paths and returns always a
            # single polygon:
            points = NumpyPoints2d(clipper.clip_polygon(points.vertices()))
        return (points,)

    def clip_paths(
        self, paths: Iterable[NumpyPath2d], max_sagitta: float
    ) -> Iterator[NumpyPath2d]:
        if self.remove_all:
            return tuple()
        if self.remove_none:
            return paths

        clipper = self.clipper
        for path in paths:
            box = BoundingBox2d(path.control_vertices())
            if clipper.is_inside(box.extmin) and clipper.is_inside(box.extmax):
                yield path
            for sub_path in path.sub_paths():
                polyline = Vec2.list(sub_path.flattening(max_sagitta, segments=4))
                for part in clipper.clip_polyline(polyline):
                    yield NumpyPath2d.from_vertices(part, close=False)

    def clip_filled_paths(
        self, paths: Iterable[NumpyPath2d], max_sagitta: float
    ) -> Iterator[NumpyPath2d]:
        if self.remove_all:
            return tuple()
        if self.remove_none:
            return paths

        clipper = self.clipper
        for path in paths:
            box = path.bbox()
            if not clipper.has_intersection(box):
                # path is complete outside the view
                continue
            if clipper.is_inside(box.extmin) and clipper.is_inside(box.extmax):
                # path is complete inside the view, no clipping required
                yield path
            else:
                # clipping is required, but only clipping of polygons is supported
                if path.has_sub_paths:
                    yield from self.clip_filled_paths(path.sub_paths(), max_sagitta)
                else:
                    yield NumpyPath2d.from_vertices(
                        clipper.clip_polygon(
                            Vec2.list(path.flattening(max_sagitta, segments=4))
                        ),
                        close=True,
                    )
