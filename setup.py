import setuptools
import glob
import sysconfig
import os

NAME = 'casperfpga'
DESCRIPTION = 'Talk to CASPER hardware devices using katcp or dcp. See https://github.com/casper-astro/casperfpga for more.'
URL = 'https://github.com/casper-astro/casperfpga'

AUTHOR  = 'Tyrone van Balla'
EMAIL   = 'tvanballa at ska.ac.za'
VERSION = '0.2.0' # Need to adopt the __version__.py format


here = os.path.abspath(os.path.dirname(__file__))

try:
    with open(os.path.join(here, 'README.md')) as readme:
        # long_description = readme.read().split('\n')[2]
        long_description = '\n{}'.format(readme.read())
except Exception as exc:
    # Probably didn't find the file?
    long_description = DESCRIPTION


# extra_compile_args = sysconfig.get_config_var('CFLAGS').split()
extra_compile_args = ['-O2', '-Wall']

data_files = ['tengbe_mmap.txt', 'tengbe_mmap_legacy.txt', 'fortygbe_mmap_legacy.txt']

setuptools.setup(
    name=NAME,
    description=DESCRIPTION,
    author=AUTHOR,
    author_email=EMAIL,
    url=URL,
    download_url='https://pypi.org/project/casperfpga',
    license='GNU GPLv2',
    long_description=long_description,
    long_description_content_type='text/markdown',
    # Specify version in-line here
    install_requires=[
        'IPython',
        'future',
        'numpy',
        'katcp>=0.9.3',
        'katversion',
        'odict',
        'setuptools',
        'tornado',
        'redis',
        'tftpy',
        'progressbar2',
        'requests',
        'circus',
        'crcmod'
    ],
    extras_require = {'test': ['pytest', 'pytest-datadir']},
    packages=['casperfpga', 'casperfpga.debug'],
    package_dir={'casperfpga': 'src', 'casperfpga.debug': 'debug'},
    package_data={'casperfpga': data_files},
    scripts=glob.glob('scripts/*'),
    setup_requires=['katversion'],
    use_katversion=True,
    ext_modules=[],
    # Required for PyPI
    keywords='casper ska meerkat fpga',
    classifiers=[
        "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
        'Programming Language :: Python :: 3.8',
        'Operating System :: OS Independent',
	    'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Scientific/Engineering :: Astronomy',
    ]
)

# end
