from typing import Tuple

import click
from rich.rule import Rule

from localstack import config
from localstack.cli import console
from localstack.utils.container_utils.container_client import ContainerConfiguration
from localstack.utils.container_utils.docker_cmd_client import CmdDockerClient
from localstack.utils.run import run_interactive
from localstack.utils.strings import short_uid

from .configurators import (
    ConfigEnvironmentConfigurator,
    ImageConfigurator,
    PortConfigurator,
    VolumeMountConfigurator,
)


@click.command("run")
@click.option("--image", type=str, required=False)
@click.option("--volume-dir", type=click.Path(file_okay=False, dir_okay=True), required=False)
@click.option("--pro", is_flag=True, default=False)
@click.option("--randomize", is_flag=True, default=False)
@click.option("--mount-source/--no-mount-source", is_flag=True, default=True)
@click.option("--mount-dependencies/--no-mount-dependencies", is_flag=True, default=False)
@click.option("--mount-entrypoints/--no-mount-entrypoints", is_flag=True, default=False)
@click.option(
    "--env",
    "-e",
    help="Additional environment variables that are passed to the LocalStack container",
    multiple=True,
    required=False,
)
@click.argument("command", nargs=-1, required=False)
def run(
    image: str = None,
    volume_dir: str = None,
    pro: bool = False,
    randomize: bool = False,
    mount_source: bool = True,
    mount_dependencies: bool = False,
    mount_entrypoints: bool = False,
    env: Tuple = (),
    command: str = None,
):
    console.print(locals())

    if command:
        entrypoint = ""
        command = list(command)
    else:
        entrypoint = None
        command = None

    status = console.status("Configuring")
    status.start()

    # setup configuration
    container_config = ContainerConfiguration(
        image_name=image,
        name=config.MAIN_CONTAINER_NAME
        if not randomize
        else config.MAIN_CONTAINER_NAME + "_" + short_uid(),
        remove=True,
        entrypoint=entrypoint,
        interactive=True,
        tty=True,
        command=command,
    )
    configurators = [
        PortConfigurator(randomize=False),
        ImageConfigurator(pro, image),
        ConfigEnvironmentConfigurator(pro, env),
        VolumeMountConfigurator(
            volume_dir=volume_dir,
            pro=pro,
            mount_source=mount_source,
            mount_dependencies=mount_dependencies,
            mount_entrypoints=mount_entrypoints,
        ),
    ]
    for configurator in configurators:
        configurator(container_config)
    console.print(container_config.__dict__)

    # run the container
    docker = CmdDockerClient()
    status.update("Creating container")
    container_id = docker.create_container_from_config(container_config)
    status.stop()
    rule = Rule("Interactive session 💻")
    console.print(rule)
    try:
        cmd = [*docker._docker_cmd(), "start", "--interactive", "--attach", container_id]
        run_interactive(cmd)
    finally:
        if container_config.remove:
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
