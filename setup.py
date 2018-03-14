from setuptools import setup, find_packages

setup(name='rangedl',
      version='1.0.1',
      license='MIT',
      description='HTTP Range Downloader',
      author='Mitsuo Heijo',
      author_email='mitsuo_h@outlook.com',
      url='http://github.com/johejo/rangedl.git',
      packages=find_packages(),
      entry_points={
          'console_scripts':
              ['rangedl = rangedl.script:main',
               'rngdl = rangedl.downloader:main'],
      },
      install_requires=['tqdm>=4.15.0',
                        'requests>=2.14.2',
                        'yarl>=1.1.0',
                        'aiosphttp>=0.1.0'],
      dependency_links=['git+https://github.com/johejo/aiosphttp.git']
      )
