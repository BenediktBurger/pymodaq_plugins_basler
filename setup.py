
from pathlib import Path

try:
    from pymodaq.resources.setup_plugin import setup as _setup
except ModuleNotFoundError:
    fallback = True
    from setuptools import setup as _setup, find_packages
    import toml
else:
    fallback = False


def setup():
    if not fallback:
        return _setup()

    config = toml.load('./plugin_info.toml')
    SHORT_PLUGIN_NAME = config['plugin-info']['SHORT_PLUGIN_NAME']
    PLUGIN_NAME = f"pymodaq_plugins_{SHORT_PLUGIN_NAME}"

    if not SHORT_PLUGIN_NAME.isidentifier():
        raise ValueError("'SHORT_PLUGIN_NAME = %s' is not a valid python identifier." % SHORT_PLUGIN_NAME)

    version_file = Path(__file__).parent.joinpath(f'src/{PLUGIN_NAME}/resources/VERSION')  # new location of the version file
    if not version_file.is_file():
        version_file = Path(__file__).parent.joinpath(f'src/{PLUGIN_NAME}/VERSION')

    with open(str(version_file), 'r') as fvers:
        version = fvers.read().strip()


    with open('README.rst') as fd:
        long_description = fd.read()

    setupOpts = dict(
        name=PLUGIN_NAME,
        description=config['plugin-info']['description'],
        long_description=long_description,
        license=config['plugin-info']['license'],
        url=config['plugin-info']['package-url'],
        author=config['plugin-info']['author'],
        author_email=config['plugin-info']['author-email'],
        classifiers=[
            "Programming Language :: Python :: 3",
            "Development Status :: 5 - Production/Stable",
            "Environment :: Other Environment",
            "Intended Audience :: Science/Research",
            "Topic :: Scientific/Engineering :: Human Machine Interfaces",
            "Topic :: Scientific/Engineering :: Visualization",
            "License :: OSI Approved :: MIT License",
            "Operating System :: OS Independent",
            "Topic :: Software Development :: Libraries :: Python Modules",
            "Topic :: Software Development :: User Interfaces",
        ], )


    return _setup(
        version=version,
        packages=find_packages(where='./src'),
        package_dir={'': 'src'},
        include_package_data=True,
        entry_points={'pymodaq.plugins': f'{SHORT_PLUGIN_NAME} = {PLUGIN_NAME}',
                      'pymodaq.pid_models': f"{SHORT_PLUGIN_NAME} = {PLUGIN_NAME}",
                      'pymodaq.extensions': f"{SHORT_PLUGIN_NAME} = {PLUGIN_NAME}"},
        install_requires=['toml', ]+config['plugin-install']['packages-required'],
        **setupOpts
    )


setup()
