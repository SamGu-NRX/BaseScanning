"""Build the COLMAP image on a side app. Prints the image id. Does not deploy a web endpoint."""

from __future__ import annotations

import modal

from server.modal_images import dockerfile_image


def main() -> None:
    app = modal.App.lookup("house-scanning-gpu-image", create_if_missing=True, environment_name="dev")
    image = dockerfile_image()
    with modal.enable_output():
        image.build(app)
    print(image.object_id)


if __name__ == "__main__":
    main()
