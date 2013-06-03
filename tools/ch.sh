#!/bin/bash
rm -rf *type
for n in `find . -name "http*"`
do
    mv $n ${n##*2F}
done
