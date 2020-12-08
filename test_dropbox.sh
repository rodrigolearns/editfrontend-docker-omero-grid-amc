#!/bin/bash

set -e
set -u
set -x

# Must be exported by the caller:
# OMERO_USER OMERO_PASS PREFIX

FILENAME=$(date +%Y%m%d-%H%M%S-%N).fake
EXEC="docker-compose exec -T omeroserver"
OMERO=/opt/omero/server/OMERO.server/bin/omero

$EXEC sh -c "mkdir -p /OMERO/DropBox/root && touch /OMERO/DropBox/root/$FILENAME"

echo -n "Checking for imported DropBox image $FILENAME "
# Retry for 4 mins
i=0
result=
while [ $i -lt 60 ]; do
    sleep 4
    result=$($EXEC $OMERO hql -q -s localhost -u $OMERO_USER -w $OMERO_PASS "SELECT COUNT (*) FROM Image WHERE name='$FILENAME'" --style plain)
    # Strip whitespace
    result=${result//[[:space:]]/}
    if [ "$result" = "0,1" ]; then
        echo
        echo "Found image: $result"
        exit 0
    fi
    if [ "$result" != "0,0" ]; then
        echo
        echo "Unexpected query result: $result"
        exit 2
    fi
    echo -n "."
    let ++i
done

echo "Failed to find image"
exit 2
