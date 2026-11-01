# Production dependencies are reused without accessing a package registry.
ARG BASE_IMAGE=sha256:7086383e8ffc8bf549cbba3f83b9f919e401f479cec883fef35cdd5b71cf1bb4
FROM ${BASE_IMAGE}
WORKDIR /lottery
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /lottery/
RUN python -m pip check \
    && python -c "from importlib.metadata import version; from pathlib import Path; from pip._vendor.packaging.requirements import Requirement; reqs=[Requirement(line.strip()) for line in Path('requirements.txt').read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')]; mismatches=[str(r)+' installed='+version(r.name) for r in reqs if not r.specifier.contains(version(r.name),prereleases=True)]; assert not mismatches, mismatches"
COPY . /lottery/
RUN python manage.py collectstatic --noinput
EXPOSE 8000
CMD ["python", "-m", "daphne", "-b", "0.0.0.0", "-p", "8000", "eventsite.asgi:application"]
