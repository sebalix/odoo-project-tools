# Copyright 2023 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import click

from .config import get_conf_key
from .utils.misc import copy_file, get_template_path
from .utils.path import build_path
from .utils.proj import get_project_manifest_key

DC_EX_FILENAME = "example.docker-compose.override.yml"
BUMPVERSION_EX_FILENAME = "example.bumpversion.cfg"


def get_bumpversion_vars():
    res = {
        "rel_path_local_addons": get_conf_key("local_src_rel_path"),
        "rel_path_version_file": get_conf_key("version_file_rel_path"),
        "bundle_addon_name": "{}_bundle".format(
            get_project_manifest_key("customer_shortname")
        ),
    }
    return res


def get_init_template_files():
    return (
        {
            "source": DC_EX_FILENAME,
            "destination": build_path("./docker-compose.override.yml"),
        },
        {
            "source": BUMPVERSION_EX_FILENAME,
            "destination": build_path("./.bumpversion.cfg"),
            "variables_getter": get_bumpversion_vars,
        },
    )


def bootstrap_files():
    for item in get_init_template_files():
        source = get_template_path(item["source"])
        dest = item["destination"]
        var_getter = item.get("variables_getter")
        if var_getter:
            with open(source) as source_fd:
                content = source_fd.read()
                with open(dest, "w") as dest_fd:
                    dest_fd.write(content.format(**var_getter()))
        else:
            copy_file(source, dest)


@click.group()
def cli():
    pass


@cli.command()
def init():
    click.echo("Preparing project...")
    bootstrap_files()


if __name__ == '__main__':
    cli()
