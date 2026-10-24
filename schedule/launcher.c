/* Launcher for the night run and the morning review. On this Mac, launchd jobs started as /bin/sh
   (or /bin/ls, /bin/cat) get "Operation not permitted" on the external volume; a job started through
   this binary may read it. If macOS ever denies it too, give this one binary Full Disk Access rather
   than /bin/sh. NIGHTLY is the script path, set at build time by schedule/install.sh. */
#include <unistd.h>
int main(void) {
    execl("/bin/sh", "sh", NIGHTLY, (char *)0);
    return 127;
}
