import os, sh
from pythonforandroid.recipe import Recipe
from pythonforandroid.logger import shprint
from pythonforandroid.util import current_directory

class LibffiRecipe(Recipe):
    """Latest libffi 7.4.2 — has pre-generated configure (no autoreconf needed)."""
    version = '7.4.2'
    url = 'https://github.com/libffi/libffi/releases/download/v{version}/libffi-{version}.tar.gz'
    depends = []

    def build_arch(self, arch):
        build_dir = self.get_build_dir(arch.arch)
        with current_directory(build_dir):
            # 7.4.2 自带预生成的 configure — 跳过 autogen.sh（需要旧 libtool 宏）
            configure_cmd = sh.Command('./configure')
            shprint(configure_cmd,
                    '--host={}'.format(arch.command_prefix),
                    '--prefix={}'.format(self.ctx.get_libjni_dir(arch)),
                    '--disable-shared',
                    '--enable-static',
                    _env=self.get_recipe_env(arch))
            shprint(sh.make, '-j4', _env=self.get_recipe_env(arch))
            shprint(sh.make, 'install', _env=self.get_recipe_env(arch))

recipe = LibffiRecipe()
