#!/usr/bin/env python
""" 
EC2/S3 Utility Classes
"""

import os
import time
import sys
import platform
from pprint import pprint

import boto
from starcluster import static
from starcluster.logger import log
from starcluster.utils import print_timing
from starcluster.hacks import register_image as _register_image

class EasyAWS(object):
    def __init__(self, aws_access_key_id, aws_secret_access_key, connection_authenticator):
        """
        Create an EasyAWS object. 

        Requires AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY from an Amazon Web Services (AWS) account
        and a CONNECTION_AUTHENTICATOR function that returns an
        authenticated AWS connection object
        """
        self.aws_access_key = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        self.connection_authenticator = connection_authenticator
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            log.debug('creating self._conn')
            self._conn = self.connection_authenticator(self.aws_access_key,
                self.aws_secret_access_key)
        return self._conn


class EasyEC2(EasyAWS):
    def __init__(self, aws_access_key_id, aws_secret_access_key, cache=False):
        super(EasyEC2, self).__init__(aws_access_key_id, aws_secret_access_key, boto.connect_ec2)
        self.cache = cache
        self._instance_response = None
        self._keypair_response = None
        self._images = None
        self._security_group_response = None
        self.s3 = EasyS3(aws_access_key_id, aws_secret_access_key, cache)

    @property
    def registered_images(self):
        if not self.cache or self._images is None:
            self._images = self.conn.get_all_images(owners=["self"])
        return self._images

    def get_registered_image(self, image_id):
        if not image_id.startswith('ami') or len(image_id) != 12:
            raise TypeError("invalid AMI name/id requested: %s" % image_id)
        for image in self.registered_images:
            if image.id == image_id:
                return image

    def get_group_or_none(self, name):
        try:
            sg = self.conn.get_all_security_groups(group_names=[name])[0]
            return sg
        except boto.exception.EC2ResponseError, e:
            pass

    def get_or_create_group(self, name, description, auth_ssh=True, auth_group_traffic=False):
        """ 
        Try to return a security group by name.
        If the group is not found, attempt to create it. 
        Description only applies to creation.

        Authorizes all traffic between members of the group
        """
        try:
            sg = self.conn.get_all_security_groups(
                groupnames=[name])[0]
            return sg
        except boto.exception.EC2ResponseError, e:
            if not name:
                return None
            log.info("Creating security group %s..." % name)
            sg = self.conn.create_security_group(name, description)
            if auth_ssh:
                sg.authorize('tcp', 22, 22, '0.0.0.0/0')
            if auth_group_traffic:
                sg.authorize(src_group=sg)
            return sg

    def run_instances(self, image_id, instance_type='m1.small', min_count=1,
                      max_count=1, key_name=None, security_groups=None,
                      placement=None):
        return self.conn.run_instances(image_id, instance_type=instance_type,
                                       min_count=min_count,
                                      max_count=max_count, key_name=key_name,
                                       security_groups=security_groups,
                                      placement=placement)

    def register_image(self, name, description=None, image_location=None,
                       architecture=None, kernel_id=None, ramdisk_id=None,
                       root_device_name=None, block_device_map=None):
        return _register_image(self.conn, name, description, image_location,
                           architecture, kernel_id, ramdisk_id,
                           root_device_name, block_device_map)

    def get_keypair(self, keypair):
        return self.conn.get_all_key_pairs(keynames=[keypair])[0]

    def __print_header(self, msg):
        print msg
        print "-" * len(msg)

    def get_image_name(self, img):
        return img.location.split('/')[1].split('.manifest.xml')[0]

    def get_instance(self, instance_id):
        try:
            res = self.conn.get_all_instances(instance_ids=[instance_id])
            return res[0].instances[0]
        except boto.exception.EC2ResponseError,e:
            return

    def get_all_instances(self, instance_ids=[]):
        reservations = self.conn.get_all_instances(instance_ids)
        instances = []
        for res in reservations:
            instances.extend(res.instances)
        return instances

    def list_all_instances(self):
        instances = self.get_all_instances()
        if not instances:
            log.info("No instances found")
        for instance in instances:
            id = instance.id
            if not id: id = 'N/A'
            dns_name = instance.dns_name
            if not dns_name: dns_name = 'N/A'
            state = instance.state
            if not state: state = 'N/A'
            private_ip = instance.private_ip_address
            if not private_ip: private_ip = 'N/A'
            public_ip = instance.ip_address
            if not public_ip: public_ip = 'N/A'
            zone = instance.placement
            if not zone: zone = 'N/A'
            print "id: %s" % id
            print "dns_name: %s" % dns_name
            print "state: %s" % state
            print "public ip: %s" % public_ip 
            print "private_ip: %s" % private_ip
            print "zone: %s" % zone
            print
            
    def list_registered_images(self):
        images = self.registered_images
        def get_key(obj):
            return str(obj.region) + ' ' + str(obj.location)
        imgs_i386 = [ img for img in images if img.architecture == "i386" ]
        imgs_i386.sort(key=get_key)
        imgs_x86_64 = [ img for img in images if img.architecture == "x86_64" ]
        imgs_x86_64.sort(key=get_key)
        self.__list_images("Your 32bit Images:", imgs_i386)
        self.__list_images("\nYour 64bit Images:", imgs_x86_64)
        print "\ntotal registered images: %d" % len(images)

    def __list_images(self, msg, imgs):
        counter = 0
        self.__print_header(msg)
        for image in imgs:
            name = self.get_image_name(image)
            print "[%d] %s %s %s" % (counter, image.id, image.region.name, name)
            counter += 1

    def remove_image_files(self, image_name, pretend=True):
        image = self.get_image(image_name)
        if image is None:
            log.error('cannot remove AMI %s' % image_name)
            return
        bucket = os.path.dirname(image.location)
        files = self.get_image_files(image_name)
        for file in files:
            if pretend:
                print file
            else:
                print 'removing file %s' % file
                file.delete()

        # recursive double check
        files = self.get_image_files(image_name)
        if len(files) != 0:
            if pretend:
                log.info('Not all files deleted, would recurse...exiting')
                return
            else:
                log.info('Not all files deleted, recursing...')
                self.remove_image_files(image_name, pretend)

    @print_timing
    def remove_image(self, image_name, pretend=True):
        image = self.get_image(image_name)
        if image is None:
            log.error('AMI %s does not exist' % image_name)
            return
        if pretend:
            log.info("Pretending to remove AMI: %s" % imageid)
        else:
            log.info("Removing AMI: %s" % image_name)

        # first remove image files
        log.info('Removing image files...')
        self.remove_image_files(image_name, pretend = pretend)

        # then deregister ami
        ami = image.id
        if pretend:
            log.info('Would run deregister_image for ami: %s)' % ami)
        else:
            log.info('Deregistering ami: %s' % ami)
            self.conn.deregister_image(ami)

    def list_image_files(self, image_name, bucket=None):
        files = self.get_image_files(image_name, bucket)
        for file in files:
            print file

    def get_zone(self, zone):
        try:
            return self.conn.get_all_zones(zones=[zone])[0]
        except IndexError,e:
            return None

    def get_image(self, image_id):
        try:
            return self.conn.get_all_images(image_ids=[image_id])[0]
        except IndexError,e:
            return None

    def get_image_files(self, image_id):
        image = self.get_image(image_id)
        bucketname = image.location.split('/')[0]
        bucket = self.s3.get_bucket(bucketname)
        files = bucket.list(prefix=os.path.basename(image.location).split('.manifest.xml')[0])
        files = [ file for file in files ]
        return files

    def list_image_files(self, image_id):
        files = self.get_image_files(image_id)
        for file in files:
            print file.name

    @property
    def instances():
        if not self.cache or self._instance_response is None:
            log.debug('instance_response = %s, cache = %s' %
            (self._instance_response, self.cache))
            self._instance_response=self.conn.get_all_instances()
        return self._instance_response
            
    @property
    def keypair():
        if not self.cache or self._keypair_response is None:
            log.debug('keypair_response = %s, cache = %s' %
            (self._keypair_response, self.cache))
            self._keypair_response = self.conn.get_all_keypairs()
        return self._keypair_response

    def get_running_instances(self):
        """ 
        TODO: write me 
        """
        pass

    def terminate_instances(self, instances=None):
        if instances is not None:
            self.conn.terminate_instances(instances)

    def get_volumes(self):
        return self.conn.get_all_volumes()

    def get_volume(self, volume_id):
        return self.conn.get_all_volumes(volume_ids=[volume_id])[0]

    def list_volumes(self):
        vols = self.get_volumes()
        if vols is not None:
            for vol in vols:
                print "volume_id: %s" % vol.id
                print "size: %sGB" % vol.size
                print "status: %s" % vol.status
                print "availability_zone: %s" % vol.zone
                if vol.snapshot_id:
                    print "snapshot_id: %s" % vol.snapshot_id
                snapshots=vol.snapshots()
                if snapshots:
                    print 'snapshots: %s' % ' '.join([snap.id for snap in snapshots])
                print

    def get_security_group(self, groupname):
        return self.conn.get_all_security_groups(groupnames=[groupname])[0]

    def get_security_groups(self):
        return self.conn.get_all_security_groups()

class EasyS3(EasyAWS):
    def __init__(self, aws_access_key_id=None, aws_secret_access_key=None, cache=False, **kwargs):
        super(EasyS3, self).__init__(aws_access_key_id, aws_secret_access_key, boto.connect_s3)
        self.cache = cache

    def bucket_exists(self, bucket_name):
        exists = (self.conn.check_bucket_exists(bucket_name).reason == 'OK')
        if not exists:
            log.error('bucket %s does not exist' % bucket_name)
        return exists

    def get_bucket(self, bucketname):
        return self.conn.get_bucket(bucketname)

    def list_bucket(self, bucketname):
        bucket = self.get_bucket(bucketname)
        for file in bucket.list():
            print file.name

    def get_buckets(self):
        buckets = self.conn.get_all_buckets()
        return buckets

    def list_buckets(self):
        for bucket in self.get_buckets():
            print bucket.name

    def get_bucket_files(self, bucketname):
        files = []
        try:
            bucket = self.get_bucket(bucketname)
        except:
            return 
        if self.bucket_exists(bucket_name):
            files = [ entry.key for entry in self.conn.list_bucket(bucket_name).entries] 
        else:
            files = []
        return files

    def show_bucket_files(self, bucket_name):
        if self.bucket_exists(bucket_name):
            files = self.get_bucket_files(bucket_name)
            for file in files:
                print file

if __name__ == "__main__":
    from starcluster.config import get_easy_ec2
    ec2 = get_easy_ec2()
    ec2.get_volume('asdf')
    #ec2.list_registered_images()