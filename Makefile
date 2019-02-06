# Requires that a virtualenv has been activated.
# FIXME: Should assert as much.
REQUIREMENTS_FILES = \
	requirements.txt

# FIXME: setuptools somehow isn't omitted.
OMIT_WHEELS = \
	zuul \
	pkg-resources \
	setuptools

.DEFAULT_GOAL := all

all: clean_env deployment_wheels

clean_env:
	PURGE_PKGS=`pip freeze | grep -vwE -- "-e|pkg-resources"`; \
	if [ -n "$$VIRTUAL_ENV" -a "$$PURGE_PKGS" ]; then \
	  echo "$$PURGE_PKGS" | xargs pip uninstall -y; \
	fi

pip_install:
	pip install wheel
	pip install --upgrade pip
	for req_txt in $(REQUIREMENTS_FILES); do \
	  pip install -r $$req_txt; \
	done

requirements.txt: pip_install
	pip freeze | \
	  grep -v $(addprefix -e , $(OMIT_WHEELS)) > \
	requirements.txt

deployment_wheels: requirements.txt
	mkdir -p wheels && \
	pip wheel -r requirements.txt -w wheels
