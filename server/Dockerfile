FROM openmicroscopy/omero-server:5.6.8

# Upload scripts for omero-server user
# COPY --chown=omero-server:omero-server scripts/slurm/* /opt/omero/server/OMERO.server/lib/scripts/slurm/
USER root
RUN yum -y update \
    && yum -y install git 
USER omero-server
RUN  cd /opt/omero/server/OMERO.server/lib/scripts/ && \
    git clone https://github.com/NL-BioImaging/omero-slurm-scripts.git slurm

# Overwrite ICE configuration
# COPY ice.config /opt/omero/server/OMERO.server/etc/templates/
# Overwrite startup run
# COPY 99-run.sh /startup/99-run.sh

# entrypoint from omero-server dockerfile: 
# run all /startup/* scripts in alphabetic/numerical order.

