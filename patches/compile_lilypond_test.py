#!/usr/bin/env python
import sys
import shutil
import os
import os.path
import datetime
import subprocess

import build_logfile
import patchy_config

# TODO: add timing information


# enable a ramdisk
# 1. copy this line into /etc/fstab:
#      tmpfs /tmp/ramdisk tmpfs size=700M,user,exec 0 0
#    (use no # when you put into /etc/fstab)
# 2. type:
#      mount /tmp/ramdisk

# OPTIONAL: increase the size=700M to size=2048M and enable this:
BUILD_ALL_DOCS = True

try:
    GIT_REPOSITORY_DIR = os.environ["LILYPOND_GIT"]
except:
    print "You must have an environment variable $LILYPOND_GIT"
    sys.exit(1)
MAIN_LOG_FILENAME = "log-%s.txt"


class NothingToDoException(Exception):
    pass


def run(cmd):
    """ runs the command inside subprocess, sends exceptions """
    cmd_split = cmd.split()
    subprocess.check_call(cmd_split)

def send_email(email_command, logfile, CC=False):
    p = os.popen(email_command, 'w')
    p.write("To: graham@percival-music.ca\n")
    if CC:
        p.write("Cc: lilypond-devel@gnu.org\n")
    p.write("From: patchy\n")
    p.write("Subject: Patchy email\n")
    loglines = open(logfile.filename).readlines()
    for line in loglines:
        p.write(line + "\n")
        print line
    p.close()


class AutoCompile():
    ### setup
    def __init__(self):
        self.config = patchy_config.PatchyConfig()

        self.date = datetime.datetime.now().strftime("%Y-%m-%d-%H")
        self.git_repository_dir = os.path.expanduser(GIT_REPOSITORY_DIR)
        self.auto_compile_dir = os.path.expanduser(
            self.config.get("compiling", "auto_compile_results_dir"))
        if not os.path.exists(self.auto_compile_dir):
            os.mkdir(self.auto_compile_dir)
        self.src_build_dir = os.path.expanduser(
            self.config.get("compiling", "build_dir"))
        self.build_dir = os.path.join(self.src_build_dir, 'build')
        self.commit = self.get_head()
        self.prev_good_commit = self.config.get("previous good compile", "last_known")
        self.logfile = build_logfile.BuildLogfile(
            os.path.join(self.auto_compile_dir,
                         str(MAIN_LOG_FILENAME % self.date)),
            self.commit)

    def debug(self):
        """ prints member variables """
        for key, value in self.__dict__.iteritems():
            print "%-20s %s" % (key, value)

    def notify(self, CC=False):
        email_command = self.config.get("notification", "smtp_command")
        if len(email_command) > 2:
            send_email(email_command, self.logfile, CC)
        else:
            print "Message for you in"
            print self.logfile.filename
    

    def get_head(self):
        os.chdir(self.git_repository_dir)
        cmd = "git rev-parse HEAD"
        p = subprocess.Popen(cmd.split(), stdout=subprocess.PIPE)
        head = p.communicate()[0].strip()
        return head

    def write_good_commit(self):
        self.config.set("previous good compile", "last_known",
            self.commit)
        self.config.save()

    ### actual building
    def make_directories(self):
        if os.path.exists(self.src_build_dir):
            shutil.rmtree(self.src_build_dir)
        os.chdir(self.git_repository_dir)
        cmd = "git checkout-index -a --prefix=%s/ " % (self.src_build_dir)
        os.system(cmd)
        os.makedirs(self.build_dir)

    def runner(self, dirname, command, issue_id=None, name=None):
        if not name:
            name = command.replace(" ", "-").replace("/", "-")
        this_logfilename = "log-%s-%s.txt" % (str(issue_id), name)
        this_logfile = open(os.path.join(self.src_build_dir, this_logfilename), 'w')
        os.chdir(dirname)
        p = subprocess.Popen(command.split(), stdout=this_logfile,
            stderr=this_logfile)
        p.wait()
        returncode = p.returncode
        this_logfile.close()
        if returncode != 0:
            self.logfile.failed_build(command,
                self.prev_good_commit, self.commit)
            raise Exception("Failed runner: %s", command)
        else:
            self.logfile.add_success(command)

    def prep(self, issue_id=None):
        self.make_directories()

    def configure(self, issue_id=None):
        self.runner(self.src_build_dir, "./autogen.sh --noconfigure",
            issue_id, "autogen.sh")
        self.runner(self.build_dir,
            "../configure --disable-optimising",
            issue_id, "configure")

    def patch(self, filename, reverse=False):
        os.chdir(self.src_build_dir)
        reverse = "--reverse" if reverse else ""
        cmd = "git apply %s %s" % (reverse, filename)
        returncode = os.system(cmd)
        if returncode != 0:
            self.logfile.failed_step("patch", filename)
            raise Exception("Failed patch: %s" % filename)
        self.logfile.add_success("applied patch %s" % filename)

    def build(self, quick_make = False, issue_id=None):
        self.runner(self.build_dir, "nice make "
            + self.config.get("compiling", "extra_make_options"),
            issue_id)
        if quick_make:
            return True
        self.runner(self.build_dir, "nice make test "
            + self.config.get("compiling", "extra_make_options"),
            issue_id)
        if BUILD_ALL_DOCS:
            self.runner(self.build_dir, "nice make doc "
                + self.config.get("compiling", "extra_make_options"),
                issue_id)

    def regtest_baseline(self, issue_id=None):
        self.runner(self.build_dir, "nice make test-baseline "
            + self.config.get("compiling", "extra_make_options"),
            issue_id)

    def regtest_check(self, issue_id=None):
        self.runner(self.build_dir, "nice make check "
            + self.config.get("compiling", "extra_make_options"),
            issue_id)

    def regtest_clean(self, issue_id=None):
        self.runner(self.build_dir, "nice make test-clean "
            + self.config.get("compiling", "extra_make_options"),
            issue_id)

    def make_regtest_show_script(self,issue_id, title):
        script_filename = os.path.join(self.auto_compile_dir,
            "show-regtests-%s.sh" % (issue_id))
        out = open(script_filename, 'w')
        out.write("echo %s\n" % title)
        out.write("firefox %s\n" % os.path.join(
            self.build_dir, "show-%i/test-results/index.html" % issue_id))
        out.close()

    def copy_regtests(self, issue_id):
        shutil.copytree(
            os.path.join(self.build_dir, "out/test-results/"),
            os.path.join(self.build_dir, "show-%i/test-results/" % issue_id))

    def merge_staging_git(self):
        if os.path.exists(self.src_build_dir):
            shutil.rmtree(self.src_build_dir)
        os.chdir(self.git_repository_dir)
        run("git fetch")
        ### don't force a new branch here; if it already exists,
        ### we want to die.  We use the "test-master-lock" branch like
        ### a lockfile
        run("git branch test-master-lock origin/master")
        run("git branch -f test-staging origin/staging")
        run("git clone -s -b test-master-lock -o local %s %s" % (self.git_repository_dir, self.src_build_dir))
        os.chdir(self.src_build_dir)
        run("git merge --ff-only local/test-staging")

        cmd = "git rev-parse HEAD"
        p = subprocess.Popen(cmd.split(), stdout=subprocess.PIPE)
        stdout, stderr = p.communicate()
        self.commit = stdout.strip()
        if self.commit == self.prev_good_commit:
            raise NothingToDoException("Nothing to do")
        self.logfile.write("Merged staging, now at:\t%s\n" % self.commit)
        run("git push local test-master-lock")

        os.makedirs(self.build_dir)


    def merge_push(self):
        os.chdir(self.git_repository_dir)
        run("git push origin test-master-lock:master")
        self.logfile.add_success("pushed to master\n")
        # TODO: update dev/staging in some way?

    def remove_test_master_lock(self):
        os.chdir(self.git_repository_dir)
        run("git branch -D test-master-lock")


    def merge_staging(self):
        """ merges the staging branch, then returns whether there
        is anything to check. """
        try:
            self.merge_staging_git()
            return True
        except NothingToDoException:
            self.logfile.add_success("No new commits in staging")
            self.notify()
        except:
            self.logfile.failed_step("merge from staging",
                "maybe somebody pushed a commit directly to master?")
            self.notify(CC=True)
        return False

    def handle_staging(self):
        self.notify()
        exit(1)
        try:
            if self.merge_staging():
                issue_id = "staging"
                self.configure(issue_id)
                self.build(quick_make=False, issue_id=issue_id)
                self.write_good_commit()
                self.merge_push()
                self.write_good_commit()
                self.notify()
        except:
            self.notify(CC=True)
        self.remove_test_master_lock()

def main(patches = None):
    autoCompile = AutoCompile()
    #autoCompile.debug()
    autoCompile.prep()
    autoCompile.configure()
    if not patches:
        #autoCompile.build()
        pass
    else:
        autoCompile.build(quick_make=True)
        autoCompile.regtest_baseline()
        for patch in patches:
            issue_id = patch[0]
            patch_filename = patch[1]
            title = patch[2]
            print "Trying %i with %s" % (issue_id, patch_filename)
            try:
                autoCompile.patch(patch_filename)
                autoCompile.build(quick_make=True, issue_id=issue_id)
                autoCompile.regtest_check(issue_id)
                autoCompile.copy_regtests(issue_id)
                autoCompile.make_regtest_show_script(issue_id, title)
                # reverse stuff
                autoCompile.patch(patch_filename, reverse=True)
                autoCompile.regtest_clean(issue_id)
            except Exception as err:
                print "Problem with issue %i" % issue_id
                print err


if __name__ == "__main__":
    #staging()
    main()

