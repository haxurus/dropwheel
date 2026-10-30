// Anim — NumberAnimation with Caelestia durations and easing curves (Theme.anim).
//
//   property int type   Anim.StandardSmall | Standard | StandardLarge | StandardExtraLarge
//                       | EmphasizedSmall | Emphasized | EmphasizedLarge | EmphasizedExtraLarge
//                       | FastSpatial | DefaultSpatial (default) | SlowSpatial
//                       | FastEffects | DefaultEffects | SlowEffects
//   Standard*/Emphasized* use durations small/normal/large/extraLarge (200/400/600/1000 ms) with the `standard` /
//   `emphasized` curve; the expressive types use their own duration and curve. `duration` and `easing.bezierCurve`
//   can still be overridden per instance.
//
//   Behavior on x { Anim { type: Anim.FastSpatial } }

import QtQuick
import "../services"

NumberAnimation {
    enum Type {
        StandardSmall = 0,
        Standard,
        StandardLarge,
        StandardExtraLarge,
        EmphasizedSmall,
        Emphasized,
        EmphasizedLarge,
        EmphasizedExtraLarge,
        FastSpatial,
        DefaultSpatial,
        SlowSpatial,
        FastEffects,
        DefaultEffects,
        SlowEffects
    }

    property int type: Anim.DefaultSpatial

    duration: {
        const d = Theme.anim.durations;
        switch (type) {
        case Anim.FastSpatial:
            return d.expressiveFastSpatial;
        case Anim.DefaultSpatial:
            return d.expressiveDefaultSpatial;
        case Anim.SlowSpatial:
            return d.expressiveSlowSpatial;
        case Anim.FastEffects:
            return d.expressiveFastEffects;
        case Anim.DefaultEffects:
            return d.expressiveDefaultEffects;
        case Anim.SlowEffects:
            return d.expressiveSlowEffects;
        }
        if (type < Anim.StandardSmall || type > Anim.EmphasizedExtraLarge)
            return d.normal;
        return [d.small, d.normal, d.large, d.extraLarge][type % 4];
    }
    easing.type: Easing.BezierSpline
    easing.bezierCurve: {
        const c = Theme.anim.curves;
        switch (type) {
        case Anim.FastSpatial:
            return c.expressiveFastSpatial;
        case Anim.DefaultSpatial:
            return c.expressiveDefaultSpatial;
        case Anim.SlowSpatial:
            return c.expressiveSlowSpatial;
        case Anim.FastEffects:
            return c.expressiveFastEffects;
        case Anim.DefaultEffects:
            return c.expressiveDefaultEffects;
        case Anim.SlowEffects:
            return c.expressiveSlowEffects;
        }
        if (type >= Anim.EmphasizedSmall && type <= Anim.EmphasizedExtraLarge)
            return c.emphasized;
        return c.standard;
    }
}
