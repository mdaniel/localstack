import os
from typing import Tuple

from localstack import config, constants
from localstack.utils.container_utils.container_client import (
    ContainerConfiguration,
    PortMappings,
    VolumeBind,
    VolumeMappings,
)
from localstack.utils.docker_utils import DOCKER_CLIENT
from localstack.utils.files import get_user_cache_dir
from localstack.utils.run import run


class VolumeMountConfigurator:
    site_packages_target_dir = "/opt/code/localstack/.venv/lib/python3.10/site-packages"

    localstack_project_dir: str
    localstack_ext_project_dir: str
    venv_path: str
    pro: bool
    mount_source: bool
    mount_dependencies: bool
    mount_entrypoints: bool

    def __init__(
        self,
        *,
        workspace_dir: str = None,
        localstack_project_dir: str = None,
        localstack_ext_project_dir: str = None,
        venv_path: str = None,
        volume_dir: str = None,
        pro: bool = False,
        mount_source: bool = True,
        mount_dependencies: bool = False,
        mount_entrypoints: bool = False,
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

        self.pro = pro
        self.mount_source = mount_source
        self.mount_dependencies = mount_dependencies
        self.mount_entrypoints = mount_entrypoints
        self.container_client = DOCKER_CLIENT

    def list_files_in_image(self, cfg: ContainerConfiguration) -> list[str]:
        if not cfg.image_name:
            raise ValueError("missing image name")

        image_id = self.container_client.inspect_image(cfg.image_name)["Id"]

        cache_dir = get_user_cache_dir() / "localstack-dev-cli"
        cache_dir.mkdir(exist_ok=True, parents=True)
        cache_file = cache_dir / f"{image_id}.files.txt"

        if cache_file.exists():
            files = cache_file.read_text().splitlines(keepends=False)
        else:
            container_id = self.container_client.create_container(image_name=cfg.image_name)
            try:
                out = run(f"docker export {container_id} | tar -t", shell=True)
                cache_file.write_text(out)
                files = out.splitlines(keepends=False)
            finally:
                self.container_client.remove_container(container_id)

        return files

    def __call__(self, cfg: ContainerConfiguration):
        # first inspect the image file system
        if self.mount_entrypoints or self.mount_dependencies:
            raise NotImplementedError
            # TODO:
            # for file in self.list_files_in_image(cfg):
            #     if file.startswith("opt/code/localstack/.venv/"):
            #         if file.endswith("entry_points.txt"):
            #             print(file)

        # configure volume mounts
        if not cfg.volumes:
            cfg.volumes = VolumeMappings()

        # basic volume directory
        if not self.volume_dir:
            raise ValueError("specify a volume directory")

        cfg.volumes.append(VolumeBind(self.volume_dir, constants.DEFAULT_VOLUME_DIR))

        if self.pro:
            # overwrite pro config
            self._configure_pro(cfg.volumes)
        else:
            # community-specific config (some source directories are different)
            self._configure_community(cfg.volumes)

    def _configure_community(self, volumes: VolumeMappings):
        if self.mount_source:
            source = os.path.join(self.localstack_project_dir, "localstack")
            if os.path.exists(source):
                volumes.add(VolumeBind(source, "/opt/code/localstack/localstack", read_only=True))

    def _configure_pro(self, volumes: VolumeMappings):
        # mount source code folders
        if self.mount_source:
            # in the pro container, localstack core is installed into the site packages
            source = os.path.join(self.localstack_project_dir, "localstack")
            if os.path.exists(source):
                target = os.path.join(self.site_packages_target_dir, "localstack")
                volumes.add(VolumeBind(source, target, read_only=True))

            # localstack_ext source files
            source = os.path.join(self.localstack_ext_project_dir, "localstack_ext")
            if os.path.exists(source):
                target = os.path.join(self.site_packages_target_dir, "localstack_ext")
                volumes.add(VolumeBind(source, target, read_only=True))


class PortConfigurator:
    def __init__(self, randomize: bool = True):
        self.randomize = randomize

    def __call__(self, cfg: ContainerConfiguration):
        if not cfg.ports:
            cfg.ports = PortMappings(bind_host=config.EDGE_BIND_HOST)

        if self.randomize:
            # TODO: randomize ports and set config accordingly (also set GATEWAY_LISTEN!)
            raise NotImplementedError
        else:
            cfg.ports.add(4566)
            cfg.ports.add([config.EXTERNAL_SERVICE_PORTS_START, config.EXTERNAL_SERVICE_PORTS_END])


class ImageConfigurator:
    def __init__(self, pro: bool, image_name: str | None):
        self.pro = pro
        self.image_name = image_name

    def __call__(self, cfg: ContainerConfiguration):
        if self.image_name:
            cfg.image_name = self.image_name
        else:
            if self.pro:
                cfg.image_name = constants.DOCKER_IMAGE_NAME_PRO
            else:
                cfg.image_name = constants.DOCKER_IMAGE_NAME


class ConfigEnvironmentConfigurator:
    def __init__(self, pro: bool, env_args: Tuple[str] = ()):
        self.pro = pro
        self.env_args = env_args

    def __call__(self, cfg: ContainerConfiguration):
        if cfg.env_vars is None:
            cfg.env_vars = {}

        if self.pro:
            from localstack_ext import config as config_ext  # noqa

        # set env vars from config
        for env_var in config.CONFIG_ENV_VARS:
            value = os.environ.get(env_var, None)
            if value is not None:
                cfg.env_vars[env_var] = value

        # add extra env vars
        for kv in self.env_args:
            kv = kv.split("=", maxsplit=1)
            k = kv[0]
            v = kv[1] if len(kv) == 2 else os.environ.get(k)
            if v is not None:
                cfg.env_vars[k] = v
