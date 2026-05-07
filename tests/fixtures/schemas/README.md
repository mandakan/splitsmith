# DTDs for renderer validation (#202)

This directory holds DTDs the test suite validates emitted XML against.
Both files are gitignored: Apple's FCPXML DTD ships only inside the
Final Cut Pro app bundle, and the xmeml DTD is third-party-distributed
under unclear terms. We don't redistribute either; you populate them
locally once and validation tests light up.

Tests gate on file presence -- a fresh checkout without DTDs has a
green suite, just with the validation tests skipped.

## FCPXML

```sh
uv run python scripts/fetch_dtds.py
```

The script probes for `Final Cut Pro.app` in `/Applications`, finds the
DTD that matches the FCPXML version splitsmith emits (1.10), and copies
it to `FCPXML_1.10.dtd` here.

Verify:

```sh
ls -l tests/fixtures/schemas/FCPXML_1.10.dtd
xmllint --noout --dtdvalid tests/fixtures/schemas/FCPXML_1.10.dtd <some.fcpxml>
```

If `Final Cut Pro.app` lives elsewhere on your machine, pass `--fcp-app`:

```sh
uv run python scripts/fetch_dtds.py --fcp-app /path/to/Final\ Cut\ Pro.app
```

## xmeml (FCP7 XML)

Not bundled with modern Apple apps. Drop a copy at
`xmeml-v5.dtd` here and the FCP7 XML validation tests will run.

The xmeml DTD has been distributed publicly with FCP7 for years and
ships in some open-source FCP7 tools. Sourcing is on you.

## What gets validated

- Emitted FCPXML for every `tests/test_fcpxml_gen.py` snapshot fixture.
- Emitted FCP7 XML for every `tests/test_fcp7xml_render.py` snapshot
  fixture.

What DTDs catch: missing required children, wrong element nesting,
illegal attribute values, malformed structure -- exactly the bugs that
produce NLE import errors. They don't catch semantic issues like
out-of-range PiP coordinates or marker frame math drift; those stay
covered by the existing structural tests.
