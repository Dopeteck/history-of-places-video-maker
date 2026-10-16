# Flow asset library integration

The renderer can now use local full-frame image plates in `assets/backgrounds/` when a scene background matches the filename. It crops/resizes the plate to the current output dimensions. If no matching image exists or the file cannot be read, the renderer falls back to the existing procedural background.

## Current limitations

- Only full-frame backgrounds are automatically consumed by this adapter.
- Character and prop JPGs in the supplied Flow pack are reference images, not transparent sprites; they must not be composited as cutouts without preprocessing.
- The supplied asset ZIP has not been uploaded into this repository by this code change. Copy its `assets/backgrounds/*` files into this repository folder to activate the background adapter.
- Scene reference stills and generated MP4s are for review/reference and are not automatically inserted as scene layers.

## Suggested mapping

Use descriptive filenames containing terms such as `palace`, `throne`, `market`, `village`, `desert`, `forest`, `ship`, or `map`. The adapter selects the best filename match deterministically.

## Validation

Run `python -m unittest tests.test_flow_background_assets tests.test_renderer` in the repository environment.
