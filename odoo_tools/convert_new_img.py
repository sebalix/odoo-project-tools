#! /usr/bin/env python3
"""
Helper to migrate "old" format project to the new image format.

Please delete this when our last project has been converted.
"""
import argparse
import configparser
import getpass
import glob
import os
import os.path as osp
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

import odoorpc


def main(args=None):
    if args is None:
        args = parse_args()

    if args.disable_module_fetching:
        installed_modules = set()
    else:
        installed_modules = get_installed_modules(
            args.instance_host,
            args.instance_port,
            args.instance_database,
            args.admin_login,
            args.admin_password,
        )
    ensure_project_root()
    move_files()
    remove_submodules(installed_modules)
    remove_files()
    copy_dockerfile()
    print_message()


def get_installed_modules(host, port, dbname, login, password):
    if port == 443:
        protocol = "jsonrpc+ssl"
    else:
        protocol = "jsonrpc"
    odoo = odoorpc.ODOO(host, port=port, protocol=protocol)
    odoo.login(dbname, login, password)
    modules = odoo.execute(
        'ir.module.module', 'search_read', [('state', '=', 'installed')], ['name']
    )
    installed_modules = set()
    for values in modules:
        installed_modules.add(values['name'])
    return installed_modules


def ensure_project_root():
    """move up in the directory tree until we are in a directory containing .gitmodule"""
    look_for = ".gitmodules"
    while not osp.isfile(look_for) and os.getcwd() != "/":
        os.chdir("..")
    if os.getcwd() == "/":
        sys.exit(
            "Unable to find project root. Relaunch command from the root of a customer project."
        )


@dataclass
class Submodule:
    name: str
    path: str = ""
    url: str = ""
    branch: str = ""

    def generate_requirements(self, installed_modules):
        """return a block concerning the submodule thatn can be inserted in a requirements.txt file.

        The block has 1 line per module which is in the repository
        """
        project_odoo_version = str(get_odoo_version())
        pypi_prefix = get_pypi_prefix()
        if self.name in ("odoo/src", "odoo/external-src/enterprise"):
            return ""
        manifests = glob.glob(self.path + "/*/__manifest__.py")
        require = [f"# {self.name}"]
        for manifest in manifests:
            addon = osp.basename(osp.dirname(manifest))
            if addon.startswith('test_'):
                continue
            addon_pypi_name = '{}-{}'.format(pypi_prefix, addon.replace('_', '-'))
            # an empty installed_modules set means we disabled fetching
            # installed modules -> in that case we get everything
            if installed_modules and addon not in installed_modules:
                # skip uninstalled addons
                continue
            with open(manifest) as man_fp:
                for line in man_fp:
                    mo = re.match(
                        r"""\s*['"]version["']\s*:\s*["']([\w.-]+)["']""", line
                    )
                    if not mo:
                        continue
                    version = mo.groups(1)[0]
                    if not version.startswith(project_odoo_version):
                        continue
                    if self.name == "odoo/external-src/odoo-cloud-platform":
                        # XXX to rework when these are published on pypi (we will still probably need to force a version
                        require.append(
                            f"{addon_pypi_name} @ git+https://github.com/camptocamp/odoo-cloud-platform@{project_odoo_version}#subdirectory=setup/{addon}"
                        )
                    else:
                        # FIXME : check for pending merges
                        require.append(
                            f"{addon_pypi_name} >= {version}, == {version}.*"
                        )
                    break
        return "\n".join(require)


def remove_submodules(installed_modules):
    """remove the submodules from the project"""
    submodules = {}
    parser = configparser.ConfigParser()
    requirements_fp = open("requirements.txt", "a")
    parser.read(".gitmodules")
    for section in parser:
        if section.startswith("submodule"):
            print(section)
            name = re.match(r"""submodule ['"]([\w/_-]+)['"]""", section).groups(1)[0]
            submodule = Submodule(name=name)
            submodules[section] = submodule
            for fieldname, value in parser[section].items():
                print(fieldname, value)
                submodule.__setattr__(fieldname, value)
            requirements = submodule.generate_requirements(installed_modules)
            requirements_fp.write(requirements)
            requirements_fp.write("\n")
    parser = configparser.ConfigParser(strict=False)
    parser.read(".git/config")
    for section in submodules:
        parser.remove_section(section)
        subprocess.run(["git", "rm", "--cached", submodules[section].path])
        shutil.rmtree(submodules[section].path)
    parser.write(open(".git/config2", "w"))
    subprocess.run(["git", "rm", "-f", ".gitmodules"])
    requirements_fp.close()
    subprocess.run(["git", "add", "requirements.txt"])


def move_files():
    if os.path.isdir("odoo/local-src/server_environment_files"):
        # the project has a server_environment_files module -> use this one
        subprocess.run(
            ["git", "rm", "-f", "-r", "odoo/addons/server_environment_files"]
        )
    if glob.glob("odoo/local-src/*bundle"):
        # the project is already using bundles -> drop the one generated by sync
        for dirname in glob.glob("odoo/addons/*bundle"):
            subprocess.run(["git", "rm", "-f", "-r", dirname])

    to_move = [
        ("odoo/VERSION", "."),
        ("odoo/migration.yml", "."),
        ("odoo/data", "."),
        ("odoo/songs", "."),
        ("odoo/patches", "."),
        ("odoo/requirements.txt", "."),
    ] + [
        (submodule, "odoo/addons")
        for submodule in glob.glob("odoo/local-src/*")
        if not submodule.endswith(
            (
                'camptocamp_tools',
                'camptocamp_website_tools',
            )
        )
    ]
    for filename, destdir in to_move:
        destname = osp.join(destdir, osp.basename(filename))
        if osp.isfile(destname):
            os.unlink(destname)
        elif osp.isdir(destname):
            shutil.rmtree(destname)
        # shutil.move(filename, destdir)
        subprocess.run(["git", "mv", "-f", filename, destdir])


def remove_files():
    """cleanup no longer needed files"""
    to_remove = [
        "tasks",
        "odoo/before-migrate-entrypoint.d",
        "odoo/bin",
        "odoo/start-entrypoint.d",
        "odoo/setup.py",
        "docs",
        "travis",
        "odoo/local-src/camptocamp_tools",
        "odoo/local-src/camptocamp_website_tools",
        # "odoo/external-src",
    ]
    for name in to_remove:
        if osp.isdir(name):
            # shutil.rmtree(name)
            subprocess.run(["git", "rm", "-f", "-r", name])

        elif osp.isfile(name):
            # os.unlink(name)
            subprocess.run(["git", "rm", "-f", name])
        else:
            raise ValueError(f'unexpected file {name}. Is it a symlink?')


def copy_dockerfile():
    shutil.move('odoo/Dockerfile', 'Dockerfile.bak')
    subprocess.run(["git", "rm", "-f", "odoo/Dockerfile"])


def get_odoo_version():
    version = open('VERSION').read().strip()
    odoo_version = int(version.split(".")[0])
    return odoo_version


def get_pypi_prefix():
    odoo_version = get_odoo_version()
    if odoo_version >= 15:
        prefix = "odoo-addon"
    else:
        prefix = "odoo-addon-%d" % odoo_version
    return prefix


def print_message():
    version = open('VERSION').read().strip()
    odoo_version = int(version.split(".")[0])
    prefix = get_pypi_prefix()
    message = f"""\
Next steps
==========

1. check the diff between Dockerfile and Dockerfile.bak (especially the environment variables)
2. check the diff between docker-compose.yml and docker-compose.yml.bak
3. check for pending merges in the OCA addons, and edit requirements.txt to match these (see below)
4. check for pending merges in odoo or enterprise and find a way to cope with this XXXXX
5. run docker build . and fix any build issues
6. run your project and be happy!

Try building the image with:

docker build .

Handling pending merges
=======================

if you have some pending merges, for instance in pending-merges/bank-payment.yml:

```
../odoo/external-src/bank-payment:
  remotes:
    camptocamp: git@github.com:camptocamp/bank-payment.git
    OCA: git@github.com:OCA/bank-payment.git
  target: camptocamp merge-branch-12345-master
  merges:
  - OCA {odoo_version}
  - OCA refs/pull/978/head
```

you need to do the following:
1. check which addon is affected by the PR
2. edit the line of that addon in requirements.txt change it from

    odoo-addon-default-register-payment-mode=={odoo_version}.1.2.0

to

{prefix}-default-register-payment-mode @ git+https://github.com/camptocamp/bank-payment@merge-branch-12345-{odoo_version}.2.0.5#subdirectory=setup/default_register_payment_mode

the format is:

{prefix}--<addon_name with "_" replaced with "-"> @ git+https://github.com/camptocamp/<repository>@merge-branch-<project_id>-{version}#subdirectory=setup/<addon_name>


"""
    print(message)


def parse_args():
    parser = argparse.ArgumentParser(
        "Project Converter", "Tool to convert projects to the new docker image format"
    )
    parser.add_argument(
        "-n",
        "--no-module-from-instance",
        action="store_true",
        dest="disable_module_fetching",
        help="don't fetch the list of installed module from a live Odoo instance",
    )
    parser.add_argument(
        "-i",
        "--instance-host",
        action="store",
        dest="instance_host",
        default="localhost",
    )
    parser.add_argument(
        "-p",
        "--instance-port",
        action="store",
        type=int,
        dest="instance_port",
        default=443,
    )
    parser.add_argument("-d", "--database", action="store", dest="instance_database")
    parser.add_argument(
        "-a", "--admin", action="store", dest="admin_login", default="admin"
    )
    args = parser.parse_args()
    if not args.disable_module_fetching:
        admin_password = getpass.getpass("Instance admin password: ")
        args.admin_password = admin_password
    return args


if __name__ == "__main__":
    main()
