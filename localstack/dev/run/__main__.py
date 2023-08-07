import os
import pty
import select
import shlex
import subprocess
import sys
import termios
import tty
from typing import Optional

import click
from rich.rule import Rule

from localstack import config, constants
from localstack.cli import console
from localstack.utils.bootstrap import get_docker_image_to_start
from localstack.utils.container_utils.container_client import (
    ContainerConfiguration,
    PortMappings,
    VolumeBind,
    VolumeMappings,
)
from localstack.utils.container_utils.docker_cmd_client import CmdDockerClient


def get_container_config():
    return ContainerConfiguration(
        image_name=get_docker_image_to_start(),
        name=config.MAIN_CONTAINER_NAME + "_dev",
        volumes=VolumeMappings(),
        remove=True,
        # FIXME: update with https://github.com/localstack/localstack/pull/7991
        ports=PortMappings(bind_host=config.EDGE_BIND_HOST),
        entrypoint=os.environ.get("ENTRYPOINT"),
        command=shlex.split(os.environ.get("CMD", "")) or None,
        env_vars={},
    )


def run_interactive(command: list[str]):
    """Run an interactive command in a subprocess. Copied from https://stackoverflow.com/a/43012138/804840"""
    # save original tty setting then set it to raw mode
    old_tty = termios.tcgetattr(sys.stdin)
    tty.setraw(sys.stdin.fileno())

    # open pseudo-terminal to interact with subprocess
    master_fd, slave_fd = pty.openpty()

    try:
        # use os.setsid() make it run in a new process group, or bash job control will not be enabled
        p = subprocess.Popen(
            command,
            preexec_fn=os.setsid,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            universal_newlines=True,
        )

        while p.poll() is None:
            r, w, e = select.select([sys.stdin, master_fd], [], [])
            if sys.stdin in r:
                d = os.read(sys.stdin.fileno(), 10240)
                os.write(master_fd, d)
                if d == b"\x04":
                    break
            elif master_fd in r:
                o = os.read(master_fd, 10240)
                if o:
                    os.write(sys.stdout.fileno(), o)
    finally:
        # restore tty settings back
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)


class DevContainerConfigurator:
    site_packages_target_dir = "/opt/code/localstack/.venv/lib/python3.10/site-packages"

    localstack_project_dir: str
    localstack_ext_project_dir: str
    venv_path: str
    env: dict[str, str]
    pro: bool
    mount_source: bool
    image_name: Optional[str]

    def __init__(
        self,
        *,
        workspace_dir: str = None,
        localstack_project_dir: str = None,
        localstack_ext_project_dir: str = None,
        venv_path: str = None,
        image_name: str = None,
        volume_dir: str = None,
        env: dict[str, str] = None,
        pro: bool = False,
        mount_source: bool = True,
        mount_entrypoints: bool = True,
    ):
        self.workspace_dir = workspace_dir or os.path.abspath(os.path.join(os.getcwd(), ".."))

        self.localstack_project_dir = localstack_project_dir or os.path.join(
            self.workspace_dir, "localstack"
        )
        self.localstack_ext_project_dir = localstack_ext_project_dir or os.path.join(
            self.workspace_dir, "localstack-ext"
        )

        self.venv_path = venv_path or os.path.join(os.getcwd(), ".venv")

        self.volume_dir = volume_dir or "/tmp/localstack"
        self.env = env or {}
        self.pro = pro
        self.image_name = image_name
        self.mount_source = mount_source
        self.mount_entrypoints = mount_entrypoints

    def configure(self, cfg: ContainerConfiguration):
        # configure volume mounts
        if not cfg.volumes:
            cfg.volumes = VolumeMappings()
        if not cfg.ports:
            cfg.ports = PortMappings()

        # basic volume directory
        if not self.volume_dir:
            raise ValueError("specify a volume directory")

        cfg.volumes.append(VolumeBind(self.volume_dir, constants.DEFAULT_VOLUME_DIR))

        # environment variables
        if self.pro:
            from localstack_ext import config as config_ext  # noqa
        for env_var in config.CONFIG_ENV_VARS:
            value = os.environ.get(env_var, None)
            if value is not None:
                cfg.env_vars[env_var] = value

        # ports
        # TODO: allow randomization (should the configurator even do this?)
        cfg.ports.add(4566)
        cfg.ports.add([4510, 4599])

        if self.pro:
            # overwrite pro config
            self._configure_pro(cfg)
        else:
            # community-specific config (some source directories are different)
            self._configure_community(cfg)

        if self.image_name:
            cfg.image_name = self.image_name

    def _configure_community(self, cfg: ContainerConfiguration):
        cfg.image_name = constants.DOCKER_IMAGE_NAME

        if self.mount_source:
            source = os.path.join(self.localstack_project_dir, "localstack")
            if os.path.exists(source):
                cfg.volumes.add(VolumeBind(source, "/opt/code/localstack/localstack"))

    def _configure_pro(self, cfg: ContainerConfiguration):
        cfg.image_name = constants.DOCKER_IMAGE_NAME_PRO

        # mount source code folders
        if self.mount_source:
            # in the pro container, localstack core is installed into the site packages
            source = os.path.join(self.localstack_project_dir, "localstack")
            if os.path.exists(source):
                target = os.path.join(self.site_packages_target_dir, "localstack")
                cfg.volumes.add(VolumeBind(source, target))

            # localstack_ext source files
            source = os.path.join(self.localstack_ext_project_dir, "localstack_ext")
            if os.path.exists(source):
                target = os.path.join(self.site_packages_target_dir, "localstack_ext")
                cfg.volumes.add(VolumeBind(source, target))


@click.command("run")
@click.option("--image", type=str, required=False)
@click.option("--volume-dir", type=click.Path(file_okay=False, dir_okay=True), required=False)
@click.option("--pro", is_flag=True, default=False)
@click.option("--randomize", is_flag=True, default=False)
@click.option("--mount-source/--no-mount-source", is_flag=True, default=True)
@click.option("--mount-entrypoints/--no-mount-entrypoints", is_flag=True, default=False)
@click.argument("command", nargs=-1, required=False)
def run(
    image: str = None,
    volume_dir: str = None,
    pro: bool = False,
    randomize: bool = False,
    mount_source: bool = True,
    mount_entrypoints: bool = False,
    command: str = None,
):
    console.print(locals())

    if command:
        entrypoint = ""
        command = list(command)
    else:
        entrypoint = None
        command = None

    docker = CmdDockerClient()

    cfg = ContainerConfiguration(
        image_name=image,
        name=config.MAIN_CONTAINER_NAME,
        volumes=VolumeMappings(),
        remove=True,
        # FIXME: update with https://github.com/localstack/localstack/pull/7991
        ports=PortMappings(bind_host=config.EDGE_BIND_HOST),
        entrypoint=entrypoint,
        interactive=True,
        tty=True,
        command=command,
        env_vars={},
    )

    dev_config = DevContainerConfigurator(
        image_name=image,
        volume_dir=volume_dir,
        pro=pro,
        # randomize=randomize,
        mount_source=mount_source,
        mount_entrypoints=mount_entrypoints,
    )

    dev_config.configure(cfg)
    console.print(cfg.__dict__)

    container_id = docker.create_container_from_config(cfg)

    rule = Rule("Interactive session 💻")
    console.print(rule)
    try:
        cmd = [*docker._docker_cmd(), "start", "--interactive", "--attach", container_id]
        run_interactive(cmd)
    finally:
        if cfg.remove:
            try:
                if docker.is_container_running(container_id):
                    docker.stop_container(container_id)
                docker.remove_container(container_id)
            except Exception:
                pass


def main():
    run()


if __name__ == "__main__":
    main()
