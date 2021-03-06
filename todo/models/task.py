# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0/LGPL 2.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is Mozilla todo app.
#
# The Initial Developer of the Original Code is
# Mozilla Foundation.
# Portions created by the Initial Developer are Copyright (C) 2010
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#   Stas Malolepszy <stas@mozilla.com>
#
# Alternatively, the contents of this file may be used under the terms of
# either the GNU General Public License Version 2 or later (the "GPL"), or
# the GNU Lesser General Public License Version 2.1 or later (the "LGPL"),
# in which case the provisions of the GPL or the LGPL are applicable instead
# of those above. If you wish to allow use of your version of this file only
# under the terms of either the GPL or the LGPL, and not to allow others to
# use your version of this file under the terms of the MPL, indicate your
# decision by deleting the provisions above and replace them with the notice
# and other provisions required by the GPL or the LGPL. If you do not delete
# the provisions above, a recipient may use your version of this file under
# the terms of any one of the MPL, the GPL or the LGPL.
#
# ***** END LICENSE BLOCK *****

from django.db import models

from life.models import Locale

from .action import ACTIVATED
from .base import Todo
from .project import Project
from .proto import ProtoTask
from .tracker import Tracker
from todo.managers import StatusManager
from todo.workflow import (NEW, ACTIVE, NEXT, ON_HOLD, RESOLVED, COMPLETED,
                           FAILED, INCOMPLETE, STATUS_CHOICES,
                           RESOLUTION_CHOICES)
from todo.signals import status_changed
    
class TaskInProject(models.Model):
    task = models.ForeignKey('Task', related_name="statuses")
    project = models.ForeignKey(Project, related_name="task_statuses")
    status = models.PositiveIntegerField(choices=STATUS_CHOICES, default=NEW)
    resolution = models.PositiveIntegerField(choices=RESOLUTION_CHOICES,
                                             null=True, blank=True)

    class Meta:
        app_label = 'todo'
        unique_together = ('task', 'project')

    def __unicode__(self):
        return '%s for %s' % (self.task, self.project)

class Task(Todo):
    prototype = models.ForeignKey(ProtoTask, related_name='tasks', null=True,
                                  blank=True)
    parent = models.ForeignKey(Tracker, related_name='tasks', null=True,
                               blank=True)
    summary = models.CharField(max_length=200, blank=True)
    locale = models.ForeignKey(Locale, related_name='tasks', null=True,
                               blank=True)
    projects = models.ManyToManyField(Project, related_name='tasks',
                                      through=TaskInProject)
    bugid = models.PositiveIntegerField(null=True, blank=True)
    alias = models.SlugField(max_length=200, null=True, blank=True)
    # a timestamp reflecting the up-to-date-ness of the Task compared to the 
    # activity in the related bug
    snapshot_ts = models.DateTimeField(null=True, blank=True)
    # a timestamp: when was the last time a Step under this Task was resolved?
    # it is set by a signal callback  (see `todo.models.log_status_change`)
    latest_resolution_ts = models.DateTimeField(null=True, blank=True)
    # a cached string representation of the task
    _repr = models.CharField(max_length=250, blank=True)
    # a cached string representation of the related prototype
    prototype_repr = models.CharField(max_length=250, blank=True)
    # a cached string representation of the related locale
    locale_repr = models.CharField(max_length=250, blank=True)

    # non-permanently cached list of next steps
    _next_steps = None
    # non-permanently cached bool describing whether or not the task has been
    # resolved on all related projects; see `is_resolved_all` below.
    _is_resolved_all = None

    objects = StatusManager()
    
    class Meta:
        app_label = 'todo'

    # a list of additional argument names that can be passed to __init__
    extra_fields = ['suffix']

    def __init__(self, *args, **kwargs):
        """Initialize a Task object.

        The method accepts one additional argument besides the ones defined by
        the model definiton: `suffix`.  If given, it will be appended to the
        parent's `alias` to create the current todo's alias.  This provides
        a breadcrumbs-like functionality.

        Alternatively, you can pass `alias` directly, which will make the
        method ignore the `suffix` and set `self.alias` to the value passed.

        """
        suffix = kwargs.pop('suffix', None)
        parent = kwargs.get('parent', None)
        alias = kwargs.get('alias', None)
        if not alias:
            prefix = parent.alias if parent else None
            bits = [bit for bit in (prefix, suffix) if bit]
            kwargs['alias'] = '-'.join(bits)
        super(Todo, self).__init__(*args, **kwargs)

    def format_repr(self, **kwargs):
        """Get a formatted string representation of the todo object."""

        _repr = self.summary
        # if kwargs are given, mask self.locale using the locale in kwargs
        locale = kwargs.get('locale', None) if kwargs else self.locale
        if locale:
            _repr = '[%s] %s' % (locale.code, _repr)
        return _repr

    def save(self, force=False, *args, **kwargs):
        if not self.id or force:
            # the task doesn't exist in the DB yet
            if self.prototype and (not self.prototype_repr or force):
                self.prototype_repr = self.prototype.summary
            if self.locale and (not self.locale_repr or force):
                self.locale_repr = unicode(self.locale)
            if not self._repr or force:
                self.repr = self.format_repr()
        super(Task, self).save(*args, **kwargs)

    def assign_to_projects(self, projects, status=NEW):
        for project in projects:
            TaskInProject.objects.create(task=self, project=project,
                                         status=status)

    def is_resolved_all(self, statuses=None):
        """Check if the task is resolved for all related projects.

        The outcome (a boolean) is temporarily cached as a property of the
        Task object.

        Arguments:
            statuses -- a list of TaskInProject objects that will be checked
                        against. Optional. If omitted, `self.statuses` will be
                        used (making a query in consequence).

        """
        if self._is_resolved_all is None:
            if statuses is None:
                statuses = self.statuses.all()
            for status in statuses:
                if status.status != RESOLVED:
                    self._is_resolved_all = False
                    break
            else:
                # if the loop finished normally
                self._is_resolved_all = True
        return self._is_resolved_all

    @property
    def code(self):
        return str(self.id)

    @models.permalink
    def get_admin_url(self):
        return ('admin:todo_task_change', [self.id])

    def clone(self):
        return self.prototype.spawn(summary=self.summary, parent=self.parent,
                                    locale=self.locale, bug=self.bug)

    def children_all(self):
        """Get the immediate children of the task.
        
        Note the this is different from self.steps which is a manager handling
        all steps under the task, no matter how deep they are in the steps
        hierarchy.

        Backstory:  Tasks do not have the `children` manager, and instead, you
        need to query for Steps with no parent (because only other Steps can be
        Steps' parents).  Since you make an actual query, you get a queryset,
        so the behavior is inconsistent with that of accessing `children` on
        Steps and Tracker (which returns a manager).

        """
        return self.steps.top_level()

    def siblings_all(self):
        """Get a QuerySet with the Task siblings of the current Task.
        
        Returns sibling Tasks only, without Trackers which might happen to be
        at the same level in the hierarchy as the Task.  In order to get the
        sibling Trackers, call Task.parent.children_all.
        
        See `todo.models.base.TodoInterface.siblings_all` for more docs.
 
        """
        return self.parent.tasks.all()

    def set_next_steps(self, steps_list):
        """Set the next steps of the task to a static list of steps."""
        self._next_steps = steps_list

    def get_next_steps(self):
        """Get the next steps in the task."""
        if not self._next_steps:
            self._next_steps = self.steps.next()
        return self._next_steps

    next_steps = property(get_next_steps, set_next_steps)

    def activate(self, user):
        "Activate the tracker across all related projects."

        self.activate_children(user)
        for status in self.statuses.all():
            status.status = ACTIVE
            status.save()
            status_changed.send(sender=status, user=user, flag=ACTIVATED)

    def resolve(self, user, project, resolution=COMPLETED):
        "Resolve the task."
        status = self.statuses.get(project=project)
        status.status = RESOLVED
        status.resolution = resolution
        status.save()
        flag = RESOLVED + resolution
        status_changed.send(sender=status, user=user, flag=flag)

    def get_bug(self):
        return self.bugid or self.alias

    def set_bug(self, val):
        "Set the `bugid` or `alias` depending on the type of the value passed."
        if isinstance(val, int):
            self.bugid = val
        else:
            self.bugid = None
            self.alias = val
    
    bug = property(get_bug, set_bug)

    def is_uptodate(self, bug_last_modified_time):
        if self.snapshot_ts is None:
            return False
        return self.snapshot_ts >= bug_last_modified_time

    def snapshot_ts_iso(self):
        if self.snapshot_ts is not None:
            return '%sZ' % self.snapshot_ts.isoformat()
        return '0'
